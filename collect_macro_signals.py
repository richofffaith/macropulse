"""Fetch a batch of FRED leading indicators and score macro stress.

Uses FRED_API_KEY from Streamlit secrets or the environment.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd
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
    "TERMCBCCALLNS",
    "DRCCLOBS",
]
AUTO_CREDIT_FRED_IDS = [
    "MVLOAS",
]
# G.20 finance-company auto book. DTCTSL is not a FRED id; we sum loans + leases.
AUTO_CAPTIVE_LOAN_FRED_ID = "DTCOLNVHFNM"
AUTO_LEASE_FRED_ID = "DTCORVHFNM"
AUTO_CAPTIVE_SERIES_ID = "DTCTSL"
# G.19 48-month new-auto finance rate; FRED id is TERMCBAUTO48NS (Board mnemonic RIFLPBCIANM48).
AUTO_RATE_FRED_ID = "TERMCBAUTO48NS"
AUTO_RATE_SERIES_ID = "RIFLPBCNC48"
# Market and fuel readings. Kept out of the stress score on purpose.
MARKET_FRED_IDS = ["SP500", "VIXCLS", "GASREGW", "DCOILWTICO"]
HISTORY_VALUE_COLUMNS = (
    ["PERMIT"]
    + [item.series_id for item in INDICATORS]
    + EXTRA_LABOR_FRED_IDS
    + CONSUMER_FRED_IDS
    + AUTO_CREDIT_FRED_IDS
    + [AUTO_CAPTIVE_SERIES_ID, AUTO_RATE_SERIES_ID]
    + ["RENT_YOY"]
    + MARKET_FRED_IDS
)
UNEMPLOYMENT_FLAG_THRESHOLD = 5.0
AUTO_DELINQUENCY_FLAG_THRESHOLD = 3.5
AUTO_DQ_CAUTION_THRESHOLD = 1.8
AUTO_DQ_STRESS_THRESHOLD = 2.5
AUTO_REPO_PIPELINE_THRESHOLD = 2.2
AUTO_DQ_QOQ_SURGE_BP = 15.0
AUTO_DQ_YOY_SURGE_BP = 40.0
CREDIT_CARD_DELINQUENCY_FLAG_THRESHOLD = 3.0


def _empty_series(name: str) -> pd.Series:
    return pd.Series(dtype=float, name=name, index=pd.DatetimeIndex([], name="date"))


def _coerce_fred_series(raw: object, name: str) -> pd.Series:
    series = pd.to_numeric(raw, errors="coerce")
    if not isinstance(series, pd.Series):
        series = pd.Series(series, dtype=float)
    series.index = pd.to_datetime(series.index, errors="coerce")
    series = series[series.index.notna()].dropna()
    series.name = name
    return series if not series.empty else _empty_series(name)


def _normalize_millions_to_billions(series: pd.Series) -> pd.Series:
    work = pd.to_numeric(series, errors="coerce").dropna()
    if work.empty:
        return series if series is not None else _empty_series("MVLOAS")
    if float(work.abs().median()) > 10_000:
        work = work / 1000.0
        work.name = series.name
        return work
    return series


def _weekly_last(series: Optional[pd.Series], column: str) -> pd.Series:
    work = pd.to_numeric(
        series if series is not None else _empty_series(column),
        errors="coerce",
    )
    if work is None or work.empty:
        return _empty_series(column)
    if not isinstance(work.index, pd.DatetimeIndex):
        work.index = pd.to_datetime(work.index, errors="coerce")
    work = work[work.index.notna()].dropna()
    if work.empty:
        return _empty_series(column)
    return work.resample("W-FRI").last().rename(column)


def _fred_transient(exc: Exception) -> bool:
    text = str(exc).lower()
    if "api key" in text or "api_key" in text:
        return False
    return any(
        token in text
        for token in ("429", "500", "502", "503", "504", "timeout", "timed out", "temporarily", "connection")
    )


def _fred_get(fred: Fred, fred_id: str) -> pd.Series:
    delay = 1.0
    for attempt in range(4):
        try:
            return fred.get_series(fred_id, observation_start=OBSERVATION_START)
        except Exception as exc:
            if attempt == 3 or not _fred_transient(exc):
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"FRED did not return {fred_id}")


def load_fred_api_key() -> str:
    from runtime_secrets import load_secret

    key = load_secret("FRED_API_KEY")
    if not key:
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
        + AUTO_CREDIT_FRED_IDS
        + [AUTO_CAPTIVE_LOAN_FRED_ID, AUTO_LEASE_FRED_ID, AUTO_RATE_FRED_ID]
        + MARKET_FRED_IDS
    )
    for fred_id in dict.fromkeys(fred_ids):
        print(f"Fetching {fred_id}...")
        try:
            raw = _fred_get(fred, fred_id)
        except Exception as exc:
            detail = re.sub(r"(api_key=)[^&\s\"']+", r"\1[redacted]", str(exc), flags=re.I)
            secret = os.environ.get("FRED_API_KEY", "").strip()
            if len(secret) >= 8:
                detail = detail.replace(secret, "[redacted]")
            print(f"  failed {fred_id}: {detail}")
            raw = _empty_series(fred_id)
        coerced = _coerce_fred_series(raw, fred_id)
        raw_by_fred_id[fred_id] = coerced
        series_map[fred_id] = coerced
        print(f"  {len(coerced):,} observations")

    for indicator in INDICATORS:
        source = raw_by_fred_id.get(indicator.source_id(), _empty_series(indicator.source_id()))
        if source is None or source.empty:
            series_map[indicator.series_id] = _empty_series(indicator.series_id)
            continue
        prepared = indicator.prepare(source) if indicator.prepare else source
        prepared = pd.to_numeric(prepared, errors="coerce").dropna()
        prepared.name = indicator.series_id
        series_map[indicator.series_id] = prepared
        if indicator.series_id != indicator.source_id():
            print(
                f"  derived {indicator.series_id}: {len(prepared):,} observations"
            )
    for claims_id in STATE_CLAIMS_IDS:
        source = raw_by_fred_id.get(claims_id, _empty_series(claims_id))
        if source is None or source.empty:
            series_map[claims_id] = _empty_series(claims_id)
            continue
        smoothed = _icsa_prepare(source).dropna()
        smoothed.name = claims_id
        series_map[claims_id] = smoothed
        print(f"  derived {claims_id} 4-week MA: {len(smoothed):,} observations")
    rent_source = raw_by_fred_id.get("CUSR0000SEHA", _empty_series("CUSR0000SEHA"))
    if rent_source is None or rent_source.empty:
        series_map["RENT_YOY"] = _empty_series("RENT_YOY")
    else:
        rent_yoy = _permit_yoy(rent_source).dropna()
        rent_yoy.name = "RENT_YOY"
        series_map["RENT_YOY"] = rent_yoy
        print(f"  derived RENT_YOY: {len(rent_yoy):,} observations")
    rate_source = raw_by_fred_id.get(AUTO_RATE_FRED_ID, _empty_series(AUTO_RATE_FRED_ID))
    series_map[AUTO_RATE_SERIES_ID] = (
        rate_source.rename(AUTO_RATE_SERIES_ID)
        if rate_source is not None and not rate_source.empty
        else _empty_series(AUTO_RATE_SERIES_ID)
    )
    if rate_source is not None and not rate_source.empty:
        print(
            f"  aliased {AUTO_RATE_FRED_ID} -> {AUTO_RATE_SERIES_ID}: "
            f"{len(series_map[AUTO_RATE_SERIES_ID]):,} observations"
        )
    if "MVLOAS" in series_map:
        series_map["MVLOAS"] = _normalize_millions_to_billions(series_map["MVLOAS"])
    if "REVOLSL" in series_map:
        series_map["REVOLSL"] = _normalize_millions_to_billions(series_map["REVOLSL"])
    loan_book = _normalize_millions_to_billions(
        series_map.get(AUTO_CAPTIVE_LOAN_FRED_ID, _empty_series(AUTO_CAPTIVE_LOAN_FRED_ID))
    )
    lease_book = _normalize_millions_to_billions(
        series_map.get(AUTO_LEASE_FRED_ID, _empty_series(AUTO_LEASE_FRED_ID))
    )
    combined = pd.to_numeric(loan_book, errors="coerce").add(
        pd.to_numeric(lease_book, errors="coerce"), fill_value=0
    ).dropna()
    combined.name = AUTO_CAPTIVE_SERIES_ID
    series_map[AUTO_CAPTIVE_SERIES_ID] = (
        combined if not combined.empty else _empty_series(AUTO_CAPTIVE_SERIES_ID)
    )
    print(
        f"  derived {AUTO_CAPTIVE_SERIES_ID} (loans+leases): "
        f"{len(series_map[AUTO_CAPTIVE_SERIES_ID]):,} observations"
    )
    return series_map


def _attach_auto_velocity(history: pd.DataFrame) -> pd.DataFrame:
    """QoQ / YoY bp surge in commercial-bank auto delinquencies (DRALACBS)."""
    if "DRALACBS" not in history.columns:
        history["DRALACBS_QOQ_BP"] = pd.NA
        history["DRALACBS_YOY_BP"] = pd.NA
        return history
    dq = pd.to_numeric(history["DRALACBS"], errors="coerce")
    if not isinstance(dq.index, pd.DatetimeIndex):
        dq.index = pd.to_datetime(dq.index, errors="coerce")
        dq = dq[dq.index.notna()]
    if dq.empty:
        history["DRALACBS_QOQ_BP"] = pd.NA
        history["DRALACBS_YOY_BP"] = pd.NA
        return history
    quarterly = dq.resample("QS").last()
    qoq = (quarterly.diff(1) * 100.0).rename("DRALACBS_QOQ_BP")
    yoy = (quarterly.diff(4) * 100.0).rename("DRALACBS_YOY_BP")
    history["DRALACBS_QOQ_BP"] = qoq.reindex(history.index, method="ffill")
    history["DRALACBS_YOY_BP"] = yoy.reindex(history.index, method="ffill")
    return history


def build_macro_history(series_map: Dict[str, pd.Series]) -> pd.DataFrame:
    """Forward-fill each series onto a weekly grid and score every week."""
    frames = []
    for column in HISTORY_VALUE_COLUMNS:
        source = series_map.get(column, pd.Series(dtype=float))
        frames.append(_weekly_last(source, column))
    history = pd.concat(frames, axis=1)
    history.index = pd.to_datetime(history.index, errors="coerce")
    history = history.loc[history.index.notna()].sort_index()
    history = history.ffill()
    needed = [item.series_id for item in INDICATORS]
    present = [col for col in needed if col in history.columns]
    if present:
        history = history.dropna(subset=present, how="any")
    history = _attach_auto_velocity(history)

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
            "cc_apr": _optional_float(row, "TERMCBCCALLNS"),
            "cc_subprime_dq": _optional_float(row, "DRCCLOBS"),
        },
        "automotive": _automotive_payload(row),
    }


def _automotive_payload(row: pd.Series) -> dict:
    auto_dq = _optional_float(row, "DRALACBS")
    qoq_bp = _optional_float(row, "DRALACBS_QOQ_BP")
    yoy_bp = _optional_float(row, "DRALACBS_YOY_BP")
    repo_flagged = bool(
        (auto_dq is not None and auto_dq >= AUTO_REPO_PIPELINE_THRESHOLD)
        or (qoq_bp is not None and qoq_bp >= AUTO_DQ_QOQ_SURGE_BP)
        or (yoy_bp is not None and yoy_bp >= AUTO_DQ_YOY_SURGE_BP)
    )
    return {
        "auto_debt": _optional_float(row, "MVLOAS"),
        "captive_leases": _optional_float(row, "DTCTSL"),
        "auto_delinquency": auto_dq,
        "car_loan_rate": _optional_float(row, "RIFLPBCNC48"),
        "dq_qoq_bp": qoq_bp,
        "dq_yoy_bp": yoy_bp,
        "repo_velocity_flagged": repo_flagged,
    }


def _optional_float(row: pd.Series, column: str) -> Optional[float]:
    if column not in row.index or pd.isna(row[column]):
        return None
    return float(row[column])


def _replace_file(path: Path, write) -> None:
    temporary = path.with_name(path.name + ".tmp")
    write(temporary)
    os.replace(temporary, path)


def save_macro_outputs(history: pd.DataFrame, snapshot: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _replace_file(HISTORY_CSV, lambda path: history.to_csv(path, index=False))
    _replace_file(SNAPSHOT_JSON, lambda path: path.write_text(json.dumps(snapshot, indent=2)))
    print(f"Wrote {HISTORY_CSV}")
    print(f"Wrote {SNAPSHOT_JSON}")


def _preserve_saved_markets(history: pd.DataFrame) -> pd.DataFrame:
    """Keep the last good market series if a refresh comes back empty."""
    if not HISTORY_CSV.exists():
        return history
    try:
        previous = pd.read_csv(HISTORY_CSV, parse_dates=["date"])
    except Exception:
        return history
    previous["date"] = pd.to_datetime(previous["date"], errors="coerce")
    previous = previous.dropna(subset=["date"]).drop_duplicates("date").sort_values("date")
    history = history.copy()
    history["date"] = pd.to_datetime(history["date"], errors="coerce")
    keep_columns = list(MARKET_FRED_IDS) + CONSUMER_FRED_IDS + [
        "RENT_YOY",
        "MVLOAS",
        "DTCTSL",
        "RIFLPBCNC48",
        "DRALACBS_QOQ_BP",
        "DRALACBS_YOY_BP",
    ]
    for column in keep_columns:
        if column not in history.columns or column not in previous.columns:
            continue
        current = pd.to_numeric(history[column], errors="coerce")
        if current.notna().any():
            continue
        prior = pd.to_numeric(previous.set_index("date")[column], errors="coerce")
        history[column] = prior.reindex(history["date"], method="ffill").to_numpy()
    return history


def collect_macro_signals() -> tuple[pd.DataFrame, dict]:
    series_map = fetch_macro_series()
    history = _preserve_saved_markets(build_macro_history(series_map))
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
        cc_apr = consumer.get("cc_apr")
        if cc_apr is not None:
            print(f"  Credit card APR             {cc_apr:>10.2f} %")
        subprime = consumer.get("cc_subprime_dq")
        if subprime is not None:
            print(f"  Smaller-bank card DQ        {subprime:>10.2f} %")
    auto = snapshot.get("automotive") or {}
    if auto:
        repo = "ON " if auto.get("repo_velocity_flagged") else "off"
        debt = auto.get("auto_debt")
        car_rate = auto.get("car_loan_rate")
        qoq = auto.get("dq_qoq_bp")
        yoy = auto.get("dq_yoy_bp")
        print("  --- Automotive credit ---")
        if debt is not None:
            billions = debt / 1000.0 if debt > 10_000 else debt
            print(f"  Motor vehicle loans         {billions:>10.1f} $bn")
        leases = auto.get("captive_leases")
        if leases is not None:
            billions = leases / 1000.0 if leases > 10_000 else leases
            print(f"  Finance-co auto leases      {billions:>10.1f} $bn")
        if car_rate is not None:
            print(f"  48-mo new-car loan rate     {car_rate:>10.2f} %")
        if qoq is not None:
            print(f"  Auto DQ QoQ                 {qoq:>10.0f} bp")
        if yoy is not None:
            print(f"  Auto DQ YoY                 {yoy:>10.0f} bp          repo velocity            {repo}")
    print("========================================\n")


def main() -> None:
    _history, snapshot = collect_macro_signals()
    print_macro_summary(snapshot)


if __name__ == "__main__":
    main()
