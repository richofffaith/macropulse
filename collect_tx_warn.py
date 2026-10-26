"""Download and clean the latest Texas Workforce Commission WARN notices."""

from __future__ import annotations

import io
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

PORTAL_URL = "https://www.twc.texas.gov/data-reports/warn-notice"
XLSX_PATTERN = (
    "https://www.twc.texas.gov/sites/default/files/oei/docs/"
    "warn-act-listings-{year}-twc.xlsx"
)
SOCRATA_CSV = "https://data.texas.gov/resource/8w53-c4f6.csv"
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
OUTPUT_CSV = PROJECT_DIR / "tx_warn_data.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": PORTAL_URL,
}

COLUMN_MAP = {
    "NOTICE_DATE": "notice_date",
    "JOB_SITE_NAME": "company",
    "COUNTY_NAME": "county",
    "CITY_NAME": "city",
    "TOTAL_LAYOFF_NUMBER": "employees",
}


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _year_links_from_portal(session: requests.Session) -> dict[int, str]:
    links: dict[int, str] = {}
    try:
        response = session.get(PORTAL_URL, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Could not scrape TWC portal ({exc}); using known file URLs.")
        return links

    soup = BeautifulSoup(response.text, "lxml")
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if ".xlsx" not in href.lower():
            continue
        url = urljoin(PORTAL_URL, href)
        for year in range(2020, datetime.now().year + 2):
            if str(year) in href or str(year) in tag.get_text():
                links[year] = url
                break
    return links


def _download_xlsx(session: requests.Session, url: str) -> bytes | None:
    """TWC often returns HTTP 202 to Python clients; curl usually succeeds."""
    try:
        completed = subprocess.run(
            [
                "curl",
                "-fsSL",
                "-A",
                HEADERS["User-Agent"],
                "-e",
                PORTAL_URL,
                url,
            ],
            check=False,
            capture_output=True,
            timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  curl failed for {url}: {exc}")
        completed = None
    if completed is not None and completed.returncode == 0 and completed.stdout.startswith(b"PK"):
        return completed.stdout

    try:
        response = session.get(url, timeout=60)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"  skip {url}: {exc}")
        return None
    if not response.content.startswith(b"PK"):
        print(f"  skip {url}: not an Excel file (HTTP {response.status_code})")
        return None
    return response.content


def _normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]
    rename = {source: dest for source, dest in COLUMN_MAP.items() if source in frame.columns}
    frame = frame.rename(columns=rename)
    needed = ["notice_date", "company", "county", "city", "employees"]
    missing = [column for column in needed if column not in frame.columns]
    if missing:
        raise ValueError(f"Texas WARN columns changed. Missing {missing}. Found {list(frame.columns)}")
    frame = frame[needed]
    frame["company"] = frame["company"].astype(str).str.strip()
    frame["city"] = frame["city"].fillna("").astype(str).str.strip()
    frame["county"] = frame["county"].fillna("").astype(str).str.strip()
    frame = frame[frame["company"].ne("") & frame["company"].ne("nan")]
    frame["notice_date"] = pd.to_datetime(frame["notice_date"], errors="coerce")
    frame["employees"] = pd.to_numeric(frame["employees"], errors="coerce")
    frame = frame.dropna(subset=["notice_date", "employees"])
    frame["employees"] = frame["employees"].astype(int)
    return frame.reset_index(drop=True)


def _load_excel_bytes(payload: bytes) -> pd.DataFrame:
    frame = pd.read_excel(io.BytesIO(payload), sheet_name=0)
    return _normalize_columns(frame)


def _fetch_socrata(session: requests.Session) -> pd.DataFrame:
    print("Falling back to Texas Open Data (Socrata) copy...")
    response = session.get(SOCRATA_CSV, params={"$limit": 50000}, timeout=60)
    response.raise_for_status()
    frame = pd.read_csv(io.StringIO(response.text))
    frame.columns = [column.strip().upper() for column in frame.columns]
    return _normalize_columns(frame)


def collect_tx_warn() -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()
    year_now = datetime.now().year
    portal_links = _year_links_from_portal(session)
    years = [year_now, year_now - 1]
    frames: list[pd.DataFrame] = []

    print(f"Opening Texas WARN portal: {PORTAL_URL}")
    for year in years:
        url = portal_links.get(year, XLSX_PATTERN.format(year=year))
        print(f"Downloading TWC {year}: {url}")
        destination = DATA_DIR / f"tx_warn_{year}.xlsx"
        payload = _download_xlsx(session, url)
        if payload:
            destination.write_bytes(payload)
        elif destination.exists() and destination.stat().st_size > 0:
            print(f"  using cached {destination.name}")
            payload = destination.read_bytes()
        if not payload:
            continue
        frames.append(_load_excel_bytes(payload))
        print(f"  {len(frames[-1]):,} rows")

    if not frames:
        frames.append(_fetch_socrata(session))

    cleaned = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["notice_date", "company", "city", "employees"])
        .sort_values(["notice_date", "company"], ascending=[False, True])
        .reset_index(drop=True)
    )
    cleaned.to_csv(OUTPUT_CSV, index=False)
    print(f"Wrote cleaned Texas data: {OUTPUT_CSV} ({len(cleaned)} rows)")
    return cleaned


def main() -> None:
    frame = collect_tx_warn()
    print(f"Texas notices: {len(frame):,}")
    print(f"Workers listed: {int(frame['employees'].sum()):,}")
    print(
        f"Notice dates: {frame['notice_date'].min().date()} to "
        f"{frame['notice_date'].max().date()}"
    )


if __name__ == "__main__":
    main()
