"""Download and clean the latest FloridaCommerce WARN notices.

Primary source: Florida REACT / FloridaJobs WARN list
(https://reactwarn.floridajobs.org/WarnList/Records).
Fallback: Hugging Face / APProjects Florida archive CSV.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

PORTAL_URL = (
    "https://www.floridajobs.org/office-directory/division-of-workforce-services/"
    "workforce-programs/reemployment-and-emergency-assistance-coordination-team-react/"
    "warn-notices"
)
REACT_URL = "https://reactwarn.floridajobs.org/WarnList/Records?year={year}"
HF_FALLBACK_URL = "https://approjects-warn-act-notices.static.hf.space/data/by-state/fl.csv"
HF_DATASET_URL = (
    "https://huggingface.co/datasets/APProjects/"
    "florida-layoffs-warn-act-notices-daily/resolve/main/data/fl_warn_notices.csv"
)
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
OUTPUT_CSV = PROJECT_DIR / "fl_warn_data.csv"
CACHE_CSV = DATA_DIR / "fl_warn_react.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": PORTAL_URL,
}

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
    "trl",
    "trail",
}
_UNIT_WORDS = {"suite", "ste", "unit", "bldg", "building", "floor", "fl", "rm", "room"}
_COMPANY_TAIL = {"inc", "llc", "co", "corp", "ltd", "lp", "pc"}


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def split_fl_company_city(blob: object) -> tuple[str, str]:
    text = str(blob or "").replace("\n", " ").strip()
    if not text or text == "nan":
        return "", ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"(BLVD|AVE|AVENUE|RD|ROAD|DR|DRIVE|ST|CT|LN|WAY)(STE|SUITE)", r"\1 \2", text, flags=re.I)
    text = re.sub(r"([a-z])([A-Z][a-z]{3,})", r"\1 \2", text)
    text = re.sub(r"([a-z])([A-Z]{3,})", r"\1 \2", text)
    text = re.sub(r"(\d)([A-Z]{2,})", r"\1 \2", text)
    loc = re.search(r"([A-Z]{2,}(?:\s+[A-Z]{2,})*),\s*FL,?\s*\d{5}(?:-\d{4})?\s*$", text)
    if not loc:
        return text.strip(" ,"), ""
    city = re.sub(r"\s+", " ", loc.group(1)).title().strip()
    city = re.sub(r"\bAfb\b", "AFB", city)
    head = text[: loc.start()].strip(" ,")
    tokens = head.split()
    cut = next((i for i, tok in enumerate(tokens) if re.match(r"^\d", tok.replace(",", ""))), len(tokens))
    if cut == len(tokens):
        for i, tok in enumerate(tokens):
            if tok.lower().rstrip(".,") in _STREET_SUFFIXES:
                cut = i
                break
    company = " ".join(tokens[:cut]).strip(" ,.-") or head
    return company, city


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [re.sub(r"\s+", " ", str(column)).strip() for column in frame.columns]
    rename = {
        "Company Name": "company_raw",
        "company": "company",
        "State Notification Date": "notice_date",
        "notice_date": "notice_date",
        "Employees Affected": "employees",
        "employees_affected": "employees",
        "employees": "employees",
        "location": "location",
        "city": "city",
        "county": "county",
    }
    lower_map = {column.lower(): column for column in frame.columns}
    resolved = {}
    for source, dest in rename.items():
        if source in frame.columns:
            resolved[source] = dest
        elif source.lower() in lower_map:
            resolved[lower_map[source.lower()]] = dest
    frame = frame.rename(columns=resolved)

    if "notice_date" not in frame.columns or "employees" not in frame.columns:
        raise ValueError(f"Florida WARN columns changed. Found {list(frame.columns)}")

    if "company" not in frame.columns:
        if "company_raw" in frame.columns:
            parsed = frame["company_raw"].map(split_fl_company_city)
            frame["company"] = parsed.map(lambda pair: pair[0])
            frame["city"] = parsed.map(lambda pair: pair[1])
        else:
            raise ValueError("Florida WARN file has no company column.")
    elif "city" not in frame.columns:
        source = frame["location"] if "location" in frame.columns else frame["company"]
        parsed = source.map(split_fl_company_city)
        if "location" in frame.columns:
            frame["city"] = parsed.map(lambda pair: pair[1])
        else:
            frame["company"] = parsed.map(lambda pair: pair[0])
            frame["city"] = parsed.map(lambda pair: pair[1])

    if "county" not in frame.columns:
        frame["county"] = ""

    out = frame[["notice_date", "company", "city", "county", "employees"]].copy()
    out["company"] = out["company"].astype(str).str.strip()
    out["city"] = out["city"].fillna("").astype(str).str.strip()
    out["county"] = out["county"].fillna("").astype(str).str.strip()
    out = out[out["company"].ne("") & out["company"].ne("nan")]
    out["notice_date"] = pd.to_datetime(out["notice_date"], format="mixed", errors="coerce")
    out["employees"] = pd.to_numeric(out["employees"], errors="coerce")
    out = out.dropna(subset=["notice_date", "employees"])
    out["employees"] = out["employees"].astype(int)
    return (
        out.drop_duplicates(subset=["notice_date", "company", "city", "employees"])
        .sort_values(["notice_date", "company"], ascending=[False, True])
        .reset_index(drop=True)
    )


def _download_react_year(session: requests.Session, year: int) -> pd.DataFrame | None:
    url = REACT_URL.format(year=year)
    print(f"Fetching Florida REACT {year}: {url}")
    try:
        response = session.get(url, timeout=90)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"  skip {year}: {exc}")
        return None
    try:
        tables = pd.read_html(io.StringIO(response.text))
    except ValueError:
        print(f"  skip {year}: no HTML table")
        return None
    if not tables:
        print(f"  skip {year}: empty page")
        return None
    cleaned = _normalize(tables[0])
    print(f"  {len(cleaned):,} rows")
    return cleaned


def _download_csv(session: requests.Session, url: str) -> pd.DataFrame | None:
    try:
        response = session.get(url, timeout=90)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"  skip {url}: {exc}")
        return None
    text = response.text.lstrip()
    if not text or "<html" in text[:80].lower():
        print(f"  skip {url}: not a CSV")
        return None
    return pd.read_csv(io.StringIO(response.text))


def collect_fl_warn() -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()
    print(f"Opening Florida WARN portal: {PORTAL_URL}")
    year_now = datetime.now().year
    frames: list[pd.DataFrame] = []
    for year in (year_now, year_now - 1):
        table = _download_react_year(session, year)
        if table is not None and not table.empty:
            frames.append(table)

    if frames:
        cleaned = (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["notice_date", "company", "city", "employees"])
            .sort_values(["notice_date", "company"], ascending=[False, True])
            .reset_index(drop=True)
        )
        CACHE_CSV.write_text(cleaned.to_csv(index=False))
    elif CACHE_CSV.exists():
        print(f"  using cached {CACHE_CSV.name}")
        cleaned = pd.read_csv(CACHE_CSV, parse_dates=["notice_date"])
    else:
        print("REACT scrape failed; trying Hugging Face Florida archive...")
        raw = _download_csv(session, HF_FALLBACK_URL)
        if raw is None:
            raw = _download_csv(session, HF_DATASET_URL)
        if raw is None:
            raise RuntimeError("Could not download Florida WARN notices from REACT or fallback.")
        cleaned = _normalize(raw)

    cleaned.to_csv(OUTPUT_CSV, index=False)
    print(f"Wrote cleaned Florida data: {OUTPUT_CSV} ({len(cleaned)} rows)")
    return cleaned


def main() -> None:
    frame = collect_fl_warn()
    print(f"Florida notices: {len(frame):,}")
    print(f"Workers listed: {int(frame['employees'].sum()):,}")
    print(
        f"Notice dates: {frame['notice_date'].min().date()} to "
        f"{frame['notice_date'].max().date()}"
    )


if __name__ == "__main__":
    main()
