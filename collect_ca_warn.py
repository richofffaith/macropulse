"""Download and clean the latest California WARN layoff notices.

The EDD does not list each notice on the web page. It publishes a spreadsheet
("Latest WARN report") that this script finds, downloads, cleans, and summarizes.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

PORTAL_URL = "https://edd.ca.gov/en/jobs_and_training/layoff_services_warn"
FALLBACK_XLSX_URL = (
    "https://edd.ca.gov/siteassets/files/jobs_and_training/warn/warn_report1.xlsx"
)
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
OUTPUT_CSV = PROJECT_DIR / "ca_warn_data.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": PORTAL_URL,
}


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def find_latest_xlsx_url(session: requests.Session) -> str:
    """Scrape the EDD WARN portal for the latest Excel report link."""
    print(f"Opening portal: {PORTAL_URL}")
    response = session.get(PORTAL_URL, timeout=60)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        label = " ".join(link.get_text(" ", strip=True).split()).lower()
        if ".xlsx" not in href.lower():
            continue
        if "latest" in label or "warn" in href.lower():
            return urljoin(PORTAL_URL, href)

    print("Could not find an XLSX link on the page; using the known file URL.")
    return FALLBACK_XLSX_URL


def download_report(session: requests.Session, url: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    destination = DATA_DIR / "warn_report_latest.xlsx"
    print(f"Downloading: {url}")
    response = session.get(url, timeout=60)
    response.raise_for_status()
    destination.write_bytes(response.content)
    print(f"Saved spreadsheet to: {destination}")
    return destination


def _clean_column_name(name: object) -> str:
    text = str(name).replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s*/\s*", "/", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _pick_detail_sheet(path: Path) -> str:
    workbook = pd.ExcelFile(path)
    for name in workbook.sheet_names:
        if "detailed warn" in name.lower():
            return name
    raise ValueError(
        "Could not find a 'Detailed WARN Report' sheet. "
        f"Available sheets: {workbook.sheet_names}"
    )


def load_and_clean(path: Path) -> pd.DataFrame:
    sheet_name = _pick_detail_sheet(path)
    print(f"Reading sheet: {sheet_name!r}")
    # Row 1 is a title banner; real column names are on the next row.
    frame = pd.read_excel(path, sheet_name=sheet_name, header=1)
    frame.columns = [_clean_column_name(column) for column in frame.columns]

    rename_map = {
        "Notice Date": "notice_date",
        "Effective Date": "effective_date",
        "Processed Date": "processed_date",
        "Company": "company",
        "County/Parish": "county",
        "Address": "address",
        "Layoff/Closure": "layoff_or_closure",
        "No. Of Employees": "employees",
        "Related Industry": "industry",
    }
    frame = frame.rename(columns=rename_map)

    expected = list(rename_map.values())
    missing = [column for column in expected if column not in frame.columns]
    if missing:
        raise ValueError(
            "The spreadsheet columns changed. Missing: "
            f"{missing}. Found: {list(frame.columns)}"
        )

    frame = frame[expected].copy()
    frame = frame.dropna(how="all")
    frame["company"] = frame["company"].astype(str).str.strip()
    frame = frame[frame["company"].ne("") & frame["company"].ne("nan")]

    for date_column in ("notice_date", "effective_date", "processed_date"):
        frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")

    frame["employees"] = pd.to_numeric(frame["employees"], errors="coerce")
    frame = frame.dropna(subset=["employees"])
    frame["employees"] = frame["employees"].astype(int)
    frame = frame.sort_values(
        ["notice_date", "company"], ascending=[False, True], na_position="last"
    )
    return frame.reset_index(drop=True)


def save_csv(frame: pd.DataFrame) -> Path:
    frame.to_csv(OUTPUT_CSV, index=False)
    print(f"Wrote cleaned data: {OUTPUT_CSV} ({len(frame)} rows)")
    return OUTPUT_CSV


def print_summary(frame: pd.DataFrame) -> None:
    total_notices = len(frame)
    total_employees = int(frame["employees"].sum())
    top_companies = (
        frame.groupby("company", as_index=False)["employees"]
        .sum()
        .sort_values("employees", ascending=False)
        .head(10)
    )
    latest_notice = frame["notice_date"].max()
    earliest_notice = frame["notice_date"].min()

    print("\n========== CA WARN summary ==========")
    print(f"Notices in the latest report: {total_notices:,}")
    print(f"Total workers listed:         {total_employees:,}")
    if pd.notna(earliest_notice) and pd.notna(latest_notice):
        print(
            "Notice date range:            "
            f"{earliest_notice.date()} to {latest_notice.date()}"
        )
    print("\nTop companies by workers listed:")
    for rank, row in enumerate(top_companies.itertuples(index=False), start=1):
        print(f"  {rank:2}. {row.company}: {row.employees:,}")
    print("=====================================\n")


def collect_warn() -> pd.DataFrame:
    session = make_session()
    xlsx_url = find_latest_xlsx_url(session)
    xlsx_path = download_report(session, xlsx_url)
    cleaned = load_and_clean(xlsx_path)
    save_csv(cleaned)
    return cleaned


def main() -> None:
    cleaned = collect_warn()
    print_summary(cleaned)


if __name__ == "__main__":
    main()
