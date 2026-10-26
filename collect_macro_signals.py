"""Fetch a batch of FRED leading indicators and score macro stress.

Uses FRED_API_KEY from the environment or a local .env file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd
from dotenv import load_dotenv
from fredapi import Fred

PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
HISTORY_CSV = DATA_DIR / "macro_history.csv"
SNAPSHOT_JSON = DATA_DIR / "macro_snapshot.json"
FRED_KEY_URL = "https://fredaccount.stlouisfed.org/apikeys"
OBSERVATION_START = "2000-01-01"

GROUP_MACRO = "macro_financials"
GROUP_HOUSING = "housing_auto"
GROUP_LABOR = "labor"


def _clip_score(value: float, benign: float, severe: float) -> float:
    """Map a raw reading onto 0 (calm) to 100 (severe)."""
    if severe == benign:
        return 0.0
    score = (value - benign) / (severe - benign) * 100.0
    return float(max(0.0, min(100.0, score)))


def _icsa_prepare(series: pd.Series) -> pd.Series:
    return series.rolling(window=4, min_periods=4).mean()


def _permit_yoy(series: pd.Series) -> pd.Series:
    monthly = series.resample("MS").last()
    return monthly.pct_change(periods=12, fill_method=None) * 100.0


@dataclass(frozen=True)
class Indicator:
    series_id: str
    name: str
    description: str
    threshold: float
    compare: str
    benign: float
    severe: float
    display_unit: str
    group: str
    fred_id: Optional[str] = None
    prepare: Optional[Callable[[pd.Series], pd.Series]] = None
    threshold_text: Optional[str] = None
    counts_toward_flags: bool = True

    def source_id(self) -> str:
        return self.fred_id or self.series_id

    def is_flagged(self, value: float) -> bool:
        if pd.isna(value):
            return False
        if self.compare == "<=":
            return value <= self.threshold
        if self.compare == "<":
            return value < self.threshold
        if self.compare == ">":
            return value > self.threshold
        return value >= self.threshold

    def score(self, value: float) -> float:
        if pd.isna(value):
            return 0.0
        return _clip_score(value, self.benign, self.severe)

    def threshold_label(self) -> str:
        if self.threshold_text:
            return self.threshold_text
        return f"{self.compare} {self.threshold:g} {self.display_unit}".strip()


INDICATORS: List[Indicator] = [
    Indicator(
        series_id="T10Y2Y",
        name="Yield Curve",
        description="10-Year minus 2-Year Treasury spread (T10Y2Y)",
        threshold=0.0,
        compare="<=",
        benign=0.50,
        severe=-0.50,
        display_unit="pp",
        group=GROUP_MACRO,
    ),
    Indicator(
        series_id="SAHMREALTIME",
        name="Sahm Rule",
        description="Real-time Sahm Rule recession indicator (SAHMREALTIME)",
        threshold=0.50,
        compare=">=",
        benign=0.20,
        severe=1.00,
        display_unit="",
        group=GROUP_MACRO,
    ),
    Indicator(
        series_id="BAMLH0A0HYM2",
        name="High-Yield Credit Spread",
        description="ICE BofA US High Yield OAS (BAMLH0A0HYM2); FRED units are percent",
        threshold=5.0,
        compare=">",
        benign=3.0,
        severe=8.0,
        display_unit="spread_pct",
        group=GROUP_MACRO,
        threshold_text="> 5.0% (500 bps)",
    ),
    Indicator(
        series_id="UMCSENT",
        name="Consumer Sentiment",
        description="University of Michigan Consumer Sentiment (UMCSENT)",
        threshold=70.0,
        compare="<=",
        benign=90.0,
        severe=50.0,
        display_unit="",
        group=GROUP_MACRO,
    ),
    Indicator(
        series_id="ICSA",
        name="Initial Jobless Claims",
        description="4-week average of initial claims (ICSA)",
        threshold=300_000,
        compare=">=",
        benign=200_000,
        severe=450_000,
        display_unit="claims",
        group=GROUP_LABOR,
        prepare=_icsa_prepare,
    ),
    Indicator(
        series_id="MSACSR",
        name="New Home Supply",
        description="Months' Supply of New Houses (MSACSR)",
        threshold=8.0,
        compare=">",
        benign=5.0,
        severe=10.0,
        display_unit="months",
        group=GROUP_HOUSING,
        threshold_text="> 8.0 months",
        counts_toward_flags=False,
    ),
    Indicator(
        series_id="PERMIT_YOY",
        name="Building Permits YoY",
        description="New Private Housing Units Authorized (PERMIT), 12-month change",
        threshold=-10.0,
        compare="<",
        benign=0.0,
        severe=-20.0,
        display_unit="yoy_pct",
        group=GROUP_HOUSING,
        fred_id="PERMIT",
        prepare=_permit_yoy,
        threshold_text="YoY decline > 10%",
        counts_toward_flags=False,
    ),
    Indicator(
        series_id="TOTALSA",
        name="Total Vehicle Sales",
        description="Total Vehicle Sales, millions of units, seasonally adjusted annual rate (TOTALSA)",
        threshold=15.0,
        compare="<",
        benign=17.0,
        severe=13.0,
        display_unit="mil",
        group=GROUP_HOUSING,
        threshold_text="< 15.0 million SAAR",
    ),
    Indicator(
        series_id="PSAVERT",
        name="Personal Saving Rate",
        description="Personal Saving Rate (PSAVERT)",
        threshold=3.5,
        compare="<",
        benign=6.0,
        severe=2.0,
        display_unit="pct",
        group=GROUP_HOUSING,
        threshold_text="< 3.5%",
    ),
]

INDICATOR_BY_ID = {item.series_id: item for item in INDICATORS}

GEO_LABOR_SERIES = {
    "All States": {"unemployment": "UNRATE", "claims": "ICSA"},
    "California": {"unemployment": "CAUR", "claims": "CAICLAIMS"},
    "Texas": {"unemployment": "TXUR", "claims": "TXICLAIMS"},
    "New York": {"unemployment": "NYUR", "claims": "NYICLAIMS"},
    "Florida": {"unemployment": "FLUR", "claims": "FLICLAIMS"},
}
STATE_UNEMPLOYMENT_IDS = ["UNRATE", "CAUR", "TXUR", "NYUR", "FLUR"]
STATE_CLAIMS_IDS = ["CAICLAIMS", "TXICLAIMS", "NYICLAIMS", "FLICLAIMS"]
EXTRA_LABOR_FRED_IDS = STATE_UNEMPLOYMENT_IDS + STATE_CLAIMS_IDS
CONSUMER_FRED_IDS = [
    "MORTGAGE30US",
    "CUSR0000SEHA",
    "DRALACBS",
    "CSUSHPINSA",
    "REVOLSL",
    "DRCCLACBS",
]
HISTORY_VALUE_COLUMNS = (
    ["PERMIT"]
    + [item.series_id for item in INDICATORS]
    + EXTRA_LABOR_FRED_IDS
    + CONSUMER_FRED_IDS
    + ["RENT_YOY"]
)
UNEMPLOYMENT_FLAG_THRESHOLD = 5.0
AUTO_DELINQUENCY_FLAG_THRESHOLD = 3.5
CREDIT_CARD_DELINQUENCY_FLAG_THRESHOLD = 3.0


def load_fred_api_key() -> str:
    load_dotenv(PROJECT_DIR / ".env")
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key or key == "paste_your_key_here":
        try:
            import streamlit as st

            key = str(st.secrets.get("FRED_API_KEY", "")).strip()
        except Exception:
            key = ""
    if not key or key == "paste_your_key_here":
        message = (
            "FRED_API_KEY is not set.\n"
            f"1. Create a free key: {FRED_KEY_URL}\n"
            "2. Copy .env.example to .env and paste the key, "
            "or add FRED_API_KEY to Streamlit secrets."
        )
        if os.environ.get("STREAMLIT_SERVER_PORT") or os.environ.get("STREAMLIT_RUNTIME"):
            raise RuntimeError(message)
        raise SystemExit(message)
    return key


def fetch_macro_series(api_key: Optional[str] = None) -> Dict[str, pd.Series]:
    """Download every configured FRED series in one batch."""
    fred = Fred(api_key=api_key or load_fred_api_key())
    raw_by_fred_id: Dict[str, pd.Series] = {}
    series_map: Dict[str, pd.Series] = {}

    fred_ids = (
        ["PERMIT"]
        + [item.source_id() for item in INDICATORS]
        + EXTRA_LABOR_FRED_IDS
        + CONSUMER_FRED_IDS
    )
    for fred_id in dict.fromkeys(fred_ids):
        print(f"Fetching {fred_id}...")
        raw = fred.get_series(fred_id, observation_start=OBSERVATION_START)
        raw = pd.to_numeric(raw, errors="coerce").dropna()
        raw.index = pd.to_datetime(raw.index)
        raw.name = fred_id
        raw_by_fred_id[fred_id] = raw
        series_map[fred_id] = raw
        print(f"  {len(raw):,} observations")

    for indicator in INDICATORS:
        source = raw_by_fred_id[indicator.source_id()]
        prepared = indicator.prepare(source) if indicator.prepare else source
        prepared = prepared.dropna()
        prepared.name = indicator.series_id
        series_map[indicator.series_id] = prepared
        if indicator.series_id != indicator.source_id():
            print(
                f"  derived {indicator.series_id}: {len(prepared):,} observations"
            )
    for claims_id in STATE_CLAIMS_IDS:
        smoothed = _icsa_prepare(raw_by_fred_id[claims_id]).dropna()
        smoothed.name = claims_id
        series_map[claims_id] = smoothed
        print(f"  derived {claims_id} 4-week MA: {len(smoothed):,} observations")
    rent_yoy = _permit_yoy(raw_by_fred_id["CUSR0000SEHA"]).dropna()
    rent_yoy.name = "RENT_YOY"
    series_map["RENT_YOY"] = rent_yoy
    print(f"  derived RENT_YOY: {len(rent_yoy):,} observations")
    return series_map


def build_macro_history(series_map: Dict[str, pd.Series]) -> pd.DataFrame:
    """Forward-fill each series onto a weekly grid and score every week."""
    frames = []
    for column in HISTORY_VALUE_COLUMNS:
        weekly = series_map[column].resample("W-FRI").last().rename(column)
        frames.append(weekly)
    history = pd.concat(frames, axis=1).sort_index()
    history = history.ffill()
    needed = [item.series_id for item in INDICATORS]
    history = history.dropna(subset=needed, how="any")

    score_cols = []
    for indicator in INDICATORS:
        values = history[indicator.series_id]
        score_name = f"{indicator.series_id}_score"
        flag_name = f"{indicator.series_id}_flag"
        history[score_name] = values.map(indicator.score)
        history[flag_name] = values.map(indicator.is_flagged)
        score_cols.append(score_name)

    history["housing_flag"] = history["MSACSR_flag"] | history["PERMIT_YOY_flag"]
    flag_cols = [
        f"{item.series_id}_flag"
        for item in INDICATORS
        if item.counts_toward_flags
    ] + ["housing_flag"]
    history["macro_stress_index"] = history[score_cols].mean(axis=1)
    history["flags_on"] = history[flag_cols].sum(axis=1).astype(int)
    history["flags_total"] = len(flag_cols)
    history.index.name = "date"
    return history.reset_index()


def _indicator_payload(indicator: Indicator, row: pd.Series) -> dict:
    value = float(row[indicator.series_id])
    return {
        "series_id": indicator.series_id,
        "name": indicator.name,
        "description": indicator.description,
        "group": indicator.group,
        "value": value,
        "display_unit": indicator.display_unit,
        "threshold": indicator.threshold,
        "threshold_label": indicator.threshold_label(),
        "flagged": bool(row[f"{indicator.series_id}_flag"]),
        "score": round(float(row[f"{indicator.series_id}_score"]), 1),
    }


def latest_snapshot(history: pd.DataFrame) -> dict:
    row = history.iloc[-1]
    indicators = [_indicator_payload(item, row) for item in INDICATORS]
    permit_level = float(row["PERMIT"]) if "PERMIT" in row and pd.notna(row["PERMIT"]) else None
    housing_flag = bool(row["housing_flag"])
    reasons = []
    if bool(row["MSACSR_flag"]):
        reasons.append("new-home supply above 8 months")
    if bool(row["PERMIT_YOY_flag"]):
        reasons.append("building permits down more than 10% YoY")
    return {
        "as_of": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
        "macro_stress_index": round(float(row["macro_stress_index"]), 1),
        "flags_on": int(row["flags_on"]),
        "flags_total": int(row["flags_total"])
        if "flags_total" in row
        else 8,
        "housing_flag": housing_flag,
        "housing_reasons": reasons,
        "permit_level": permit_level,
        "indicators": indicators,
        "labor_markets": {
            geo: {
                "unemployment_id": ids["unemployment"],
                "unemployment": float(row[ids["unemployment"]])
                if pd.notna(row.get(ids["unemployment"]))
                else None,
                "unemployment_flagged": bool(
                    pd.notna(row.get(ids["unemployment"]))
                    and float(row[ids["unemployment"]]) >= UNEMPLOYMENT_FLAG_THRESHOLD
                ),
                "claims_id": ids["claims"],
                "claims": float(row[ids["claims"]])
                if pd.notna(row.get(ids["claims"]))
                else None,
                "claims_flagged": bool(
                    ids["claims"] == "ICSA" and bool(row.get("ICSA_flag", False))
                ),
            }
            for geo, ids in GEO_LABOR_SERIES.items()
            if ids["unemployment"] in row.index and ids["claims"] in row.index
        },
        "consumer": {
            "mortgage_rate": _optional_float(row, "MORTGAGE30US"),
            "rent_yoy": _optional_float(row, "RENT_YOY"),
            "auto_delinquency": _optional_float(row, "DRALACBS"),
            "auto_delinquency_flagged": bool(
                pd.notna(row.get("DRALACBS"))
                and float(row["DRALACBS"]) > AUTO_DELINQUENCY_FLAG_THRESHOLD
            ),
            "home_price_index": _optional_float(row, "CSUSHPINSA"),
            "saving_rate": _optional_float(row, "PSAVERT"),
            "saving_rate_flagged": bool(row.get("PSAVERT_flag", False)),
            "cc_delinquency": _optional_float(row, "DRCCLACBS"),
            "cc_delinquency_flagged": bool(
                pd.notna(row.get("DRCCLACBS"))
                and float(row["DRCCLACBS"])
                > CREDIT_CARD_DELINQUENCY_FLAG_THRESHOLD
            ),
            "revolving_credit": _optional_float(row, "REVOLSL"),
        },
    }


def _optional_float(row: pd.Series, column: str) -> Optional[float]:
    if column not in row.index or pd.isna(row[column]):
        return None
    return float(row[column])


def save_macro_outputs(history: pd.DataFrame, snapshot: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    history.to_csv(HISTORY_CSV, index=False)
    SNAPSHOT_JSON.write_text(json.dumps(snapshot, indent=2))
    print(f"Wrote {HISTORY_CSV}")
    print(f"Wrote {SNAPSHOT_JSON}")


def collect_macro_signals() -> tuple[pd.DataFrame, dict]:
    series_map = fetch_macro_series()
    history = build_macro_history(series_map)
    snapshot = latest_snapshot(history)
    save_macro_outputs(history, snapshot)
    return history, snapshot


def print_macro_summary(snapshot: dict) -> None:
    print("\n========== Macro Stress Index ==========")
    print(f"As of {snapshot['as_of']}")
    print(
        f"Index: {snapshot['macro_stress_index']:.1f}%  "
        f"({snapshot['flags_on']} of {snapshot['flags_total']} thresholds breached)"
    )
    housing = "ON " if snapshot["housing_flag"] else "off"
    reason = (
        "; ".join(snapshot["housing_reasons"])
        if snapshot["housing_reasons"]
        else "inventory and permits both clear"
    )
    print(f"  Housing (composite)          {housing}  {reason}")
    for item in snapshot["indicators"]:
        flag = "ON " if item["flagged"] else "off"
        unit = f" {item['display_unit']}" if item["display_unit"] else ""
        print(
            f"  {item['name']:<26} {item['value']:>10,.2f}{unit:<10} "
            f"{item['threshold_label']:<24} "
            f"score {item['score']:>5.1f}  {flag}"
        )
    consumer = snapshot.get("consumer") or {}
    if consumer:
        auto_flag = "ON " if consumer.get("auto_delinquency_flagged") else "off"
        mortgage = consumer.get("mortgage_rate")
        rent = consumer.get("rent_yoy")
        auto = consumer.get("auto_delinquency")
        print("  --- Consumer ---")
        if mortgage is not None:
            print(f"  30-yr mortgage              {mortgage:>10.2f} %")
        if rent is not None:
            print(f"  Rent inflation YoY          {rent:>10.2f} %")
        if auto is not None:
            print(f"  Auto delinquency            {auto:>10.2f} %          > 3.5%                   {auto_flag}")
        saving = consumer.get("saving_rate")
        cc_dq = consumer.get("cc_delinquency")
        cc_flag = "ON " if consumer.get("cc_delinquency_flagged") else "off"
        revolving = consumer.get("revolving_credit")
        if saving is not None:
            print(f"  Personal saving rate        {saving:>10.2f} %")
        if cc_dq is not None:
            print(
                f"  Credit card delinquency     {cc_dq:>10.2f} %          > 3.0%                   {cc_flag}"
            )
        if revolving is not None:
            billions = revolving / 1000.0 if revolving > 10_000 else revolving
            print(f"  Revolving credit            {billions:>10.1f} $bn")
    print("========================================\n")


def main() -> None:
    _history, snapshot = collect_macro_signals()
    print_macro_summary(snapshot)


if __name__ == "__main__":
    main()
