"""Combine California WARN layoffs with the FRED T10Y2Y yield spread.

Install once (from the project folder, with the virtual environment on):

    pip install fredapi python-dotenv

Get a free API key: https://fredaccount.stlouisfed.org/apikeys

Then either:

    export FRED_API_KEY=your_key_here

or copy `.env.example` to `.env` and paste the key there.

    python recession_signal.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from fredapi import Fred

from collect_ca_warn import OUTPUT_CSV
from us_warn import warn_status

PROJECT_DIR = Path(__file__).resolve().parent
T10Y2Y_SERIES_ID = "T10Y2Y"
FRED_KEY_URL = "https://fredaccount.stlouisfed.org/apikeys"


def load_warn_table() -> pd.DataFrame:
    if not OUTPUT_CSV.exists():
        raise FileNotFoundError(
            f"Missing {OUTPUT_CSV.name}. Run: python collect_ca_warn.py"
        )
    frame = pd.read_csv(
        OUTPUT_CSV,
        parse_dates=["notice_date", "effective_date", "processed_date"],
    )
    return frame


def load_fred_api_key() -> str:
    load_dotenv(PROJECT_DIR / ".env")
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key or key == "paste_your_key_here":
        raise SystemExit(
            "FRED_API_KEY is not set.\n"
            f"1. Create a free key: {FRED_KEY_URL}\n"
            "2. In Terminal:  export FRED_API_KEY=your_key_here\n"
            "   or copy .env.example to .env and paste the key there."
        )
    return key


def fetch_t10y2y(api_key: str) -> pd.Series:
    """Download the 10-Year minus 2-Year Treasury yield spread from FRED."""
    print(f"Connecting to FRED API for series {T10Y2Y_SERIES_ID}...")
    fred = Fred(api_key=api_key)
    series = fred.get_series(T10Y2Y_SERIES_ID)
    series = pd.to_numeric(series, errors="coerce").dropna()
    series.name = T10Y2Y_SERIES_ID
    print(f"Downloaded {len(series):,} daily observations of {T10Y2Y_SERIES_ID}.")
    return series


def yield_curve_status(spread: pd.Series) -> dict:
    latest_date = spread.index.max()
    latest_value = float(spread.loc[latest_date])
    inverted_now = latest_value < 0

    consecutive = 0
    for value in reversed(spread.tolist()):
        if value < 0:
            consecutive += 1
        else:
            break

    recent = spread.loc[spread.index >= latest_date - pd.Timedelta(days=365)]
    inverted_days_year = int((recent < 0).sum())
    return {
        "latest_date": latest_date,
        "latest_value": latest_value,
        "inverted_now": inverted_now,
        "consecutive_inverted_days": consecutive,
        "inverted_days_past_year": inverted_days_year,
        "year_min": float(recent.min()),
        "year_max": float(recent.max()),
    }


def print_recession_signal(warn: dict, curve: dict) -> None:
    curve_on = curve["inverted_now"]
    labor_on = warn["rising"]
    active = int(curve_on) + int(labor_on)

    print("\n========== Recession Signal Summary ==========")
    print("This is a two-indicator snapshot, not a forecast.\n")

    print("1) Yield curve  |  FRED T10Y2Y (10-year minus 2-year)")
    print(
        f"   Latest spread: {curve['latest_value']:+.2f} percentage points "
        f"on {curve['latest_date'].date()}"
    )
    print(
        "   Status:        "
        + (
            "INVERTED  (short-term rates above long-term)"
            if curve_on
            else "not inverted"
        )
    )
    if curve_on:
        print(
            f"   Consecutive inverted sessions: {curve['consecutive_inverted_days']}"
        )
    print(
        f"   Past year: {curve['inverted_days_past_year']} inverted sessions; "
        f"range {curve['year_min']:+.2f} to {curve['year_max']:+.2f}"
    )
    print(f"   Signal:        {'ON' if curve_on else 'off'}\n")

    print("2) California layoffs  |  EDD WARN notices")
    print(
        f"   Report window:  {warn['earliest'].date()} to {warn['latest'].date()}"
    )
    print(
        f"   Full report:    {warn['notices']:,} notices, "
        f"{warn['workers']:,} workers listed"
    )
    print(
        f"   Last 30 days:   {warn['last_30_notices']:,} notices, "
        f"{warn['last_30_workers']:,} workers"
    )
    print(
        f"   Prior 30 days:  {warn['prior_30_workers']:,} workers "
        f"({'up' if warn['rising'] else 'down or flat'} vs prior window)"
    )
    print(f"   Signal:        {'ON  (layoffs accelerating)' if labor_on else 'off'}\n")

    print(f"Combined: {active} of 2 stress signals active")
    if active == 2:
        print("Reading: both the Treasury curve and CA WARN point to stress.")
    elif curve_on:
        print("Reading: the yield curve is inverted; CA WARN is not accelerating.")
    elif labor_on:
        print("Reading: CA WARN layoffs are accelerating; the yield curve is not inverted.")
    else:
        print("Reading: neither indicator is flashing stress right now.")
    print("==============================================\n")


def main() -> None:
    api_key = load_fred_api_key()
    spread = fetch_t10y2y(api_key)
    warn = warn_status(load_warn_table())
    curve = yield_curve_status(spread)
    print_recession_signal(warn, curve)


if __name__ == "__main__":
    main()
