"""Standardize California, Texas, New York, and Florida WARN notices into one table."""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from collect_ca_warn import collect_warn as collect_ca_warn
from collect_fl_warn import collect_fl_warn
from collect_ny_warn import collect_ny_warn
from collect_tx_warn import collect_tx_warn

PROJECT_DIR = Path(__file__).resolve().parent
US_WARN_CSV = PROJECT_DIR / "us_warn_data.csv"
STANDARD_COLUMNS = [
    "State",
    "Notice Date",
    "Company",
    "City",
    "County",
    "Employees Affected",
]


_STREET_SUFFIXES = {
    "st",
    "street",
    "ave",
    "avenue",
    "blvd",
    "boulevard",
    "dr",
    "drive",
    "rd",
    "road",
    "ln",
    "lane",
    "way",
    "pkwy",
    "parkway",
    "hwy",
    "highway",
    "ct",
    "court",
    "pl",
    "place",
    "cir",
    "circle",
}
_UNIT_WORDS = {"suite", "ste", "unit", "bldg", "building", "floor", "fl", "rm", "room"}


def city_from_ca_address(address: object) -> str:
    text = str(address or "").strip()
    if not text or text == "nan":
        return ""
    match = re.search(r"^(.*)\s+CA\s+\d{5}(?:-\d{4})?\s*$", text, flags=re.I)
    prefix = match.group(1) if match else text
    tokens = re.sub(r"[#,]", " ", prefix).split()
    while tokens:
        last = tokens[-1].lower().rstrip(".")
        if last in _UNIT_WORDS or re.fullmatch(r"[a-z]?\d+[a-z]?", last):
            tokens.pop()
            continue
        if len(last) <= 2 and last.isalpha() and last not in {"st"}:
            tokens.pop()
            continue
        break
    suffix_at = -1
    for index, token in enumerate(tokens):
        if token.lower().rstrip(".") in _STREET_SUFFIXES:
            suffix_at = index
    city_tokens = tokens[suffix_at + 1 :] if suffix_at >= 0 else tokens[-3:]
    cleaned_city = []
    index = 0
    while index < len(city_tokens):
        token = city_tokens[index].lower().rstrip(".")
        if token in _UNIT_WORDS:
            index += 2 if index + 1 < len(city_tokens) else 1
            continue
        cleaned_city.append(city_tokens[index])
        index += 1
    city = " ".join(cleaned_city).strip(" ,")
    return city if city and not re.search(r"\d", city) else ""


def standardize_california(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "State": "California",
            "Notice Date": pd.to_datetime(frame["notice_date"], errors="coerce"),
            "Company": frame["company"].astype(str).str.strip(),
            "City": frame.get("address", pd.Series([""] * len(frame))).map(
                city_from_ca_address
            ),
            "County": frame["county"].fillna("").astype(str).str.strip(),
            "Employees Affected": pd.to_numeric(frame["employees"], errors="coerce"),
        }
    )
    return out.dropna(subset=["Notice Date", "Employees Affected", "Company"])


def standardize_new_york(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "State": "New York",
            "Notice Date": pd.to_datetime(frame["notice_date"], errors="coerce"),
            "Company": frame["company"].astype(str).str.strip(),
            "City": frame.get("city", pd.Series([""] * len(frame)))
            .fillna("")
            .astype(str)
            .str.strip(),
            "County": frame["county"].fillna("").astype(str).str.strip(),
            "Employees Affected": pd.to_numeric(frame["employees"], errors="coerce"),
        }
    )
    return out.dropna(subset=["Notice Date", "Employees Affected", "Company"])


def standardize_florida(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "State": "Florida",
            "Notice Date": pd.to_datetime(frame["notice_date"], errors="coerce"),
            "Company": frame["company"].astype(str).str.strip(),
            "City": frame.get("city", pd.Series([""] * len(frame)))
            .fillna("")
            .astype(str)
            .str.strip(),
            "County": frame["county"].fillna("").astype(str).str.strip()
            if "county" in frame.columns
            else "",
            "Employees Affected": pd.to_numeric(frame["employees"], errors="coerce"),
        }
    )
    return out.dropna(subset=["Notice Date", "Employees Affected", "Company"])


def standardize_texas(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "State": "Texas",
            "Notice Date": pd.to_datetime(frame["notice_date"], errors="coerce"),
            "Company": frame["company"].astype(str).str.strip(),
            "City": frame.get("city", pd.Series([""] * len(frame)))
            .fillna("")
            .astype(str)
            .str.strip(),
            "County": frame["county"].fillna("").astype(str).str.strip(),
            "Employees Affected": pd.to_numeric(frame["employees"], errors="coerce"),
        }
    )
    return out.dropna(subset=["Notice Date", "Employees Affected", "Company"])


def combine_us_warn(
    california: pd.DataFrame,
    texas: pd.DataFrame,
    new_york: pd.DataFrame | None = None,
    florida: pd.DataFrame | None = None,
    *,
    save: bool = True,
) -> pd.DataFrame:
    parts = [standardize_california(california), standardize_texas(texas)]
    if new_york is not None:
        parts.append(standardize_new_york(new_york))
    if florida is not None:
        parts.append(standardize_florida(florida))
    combined = pd.concat(parts, ignore_index=True)
    combined["Employees Affected"] = combined["Employees Affected"].astype(int)
    combined = combined.sort_values(
        ["Notice Date", "State", "Company"], ascending=[False, True, True]
    ).reset_index(drop=True)
    combined = combined[STANDARD_COLUMNS]
    if save:
        combined.to_csv(US_WARN_CSV, index=False)
        print(f"Wrote combined WARN file: {US_WARN_CSV} ({len(combined)} rows)")
    return combined


def collect_us_warn() -> pd.DataFrame:
    california = collect_ca_warn()
    texas = collect_tx_warn()
    new_york = collect_ny_warn()
    florida = collect_fl_warn()
    return combine_us_warn(california, texas, new_york, florida)


def load_us_warn() -> pd.DataFrame:
    if not US_WARN_CSV.exists():
        return collect_us_warn()
    frame = pd.read_csv(US_WARN_CSV, parse_dates=["Notice Date"])
    return frame


def warn_status(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {
            "notices": 0,
            "workers": 0,
            "earliest": pd.NaT,
            "latest": pd.NaT,
            "last_30_workers": 0,
            "last_30_notices": 0,
            "prior_30_workers": 0,
            "rising": False,
        }
    if "Notice Date" in frame.columns:
        dates = pd.to_datetime(frame["Notice Date"], errors="coerce")
        workers = pd.to_numeric(frame["Employees Affected"], errors="coerce")
    else:
        dates = pd.to_datetime(frame["notice_date"], errors="coerce")
        workers = pd.to_numeric(frame["employees"], errors="coerce")

    latest = dates.max()
    window_start = latest - pd.Timedelta(days=30)
    prior_start = window_start - pd.Timedelta(days=30)
    in_last_30 = dates >= window_start
    in_prior_30 = (dates >= prior_start) & (dates < window_start)
    last_30_workers = int(workers[in_last_30].fillna(0).sum())
    prior_30_workers = int(workers[in_prior_30].fillna(0).sum())
    return {
        "notices": int(len(frame)),
        "workers": int(workers.fillna(0).sum()),
        "earliest": dates.min(),
        "latest": latest,
        "last_30_workers": last_30_workers,
        "last_30_notices": int(in_last_30.sum()),
        "prior_30_workers": prior_30_workers,
        "rising": last_30_workers > prior_30_workers,
    }
