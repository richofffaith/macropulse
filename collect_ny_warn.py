"""Download and clean the latest New York State WARN notices.

Primary source: NYS DOL WARN dashboard Tableau CSV export
(https://dol.ny.gov/warn-dashboard).
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd
import requests

PORTAL_URL = "https://dol.ny.gov/warn-dashboard"
TABLEAU_CSV_URL = (
    "https://public.tableau.com/views/"
    "WorkerAdjustmentRetrainingNotificationWARN/WARN.csv?:showVizHome=no"
)
HF_FALLBACK_URL = (
    "https://huggingface.co/datasets/APProjects/"
    "new-york-layoffs-warn-act-notices-daily/resolve/main/data/ny_warn_notices.csv"
)
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
OUTPUT_CSV = PROJECT_DIR / "ny_warn_data.csv"
CACHE_CSV = DATA_DIR / "ny_warn_tableau.csv"

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
    "pl",
    "place",
    "fl",
    "floor",
    "quadrangle",
}
_UNIT_WORDS = {"suite", "ste", "unit", "floor", "fl", "rm", "room"}


def city_from_ny_address(address: object) -> str:
    text = str(address or "").strip()
    if not text or text == "nan":
        return ""
    match = re.search(r"([^,]+),\s*NY\b", text, flags=re.I)
    left = match.group(1).strip() if match else text
    chunks = [part.strip() for part in re.split(r"\s{2,}", left) if part.strip()]
    if len(chunks) >= 2 and not re.search(r"\d", chunks[-1]):
        return chunks[-1]
    tokens = re.sub(r"[#]", " ", left).split()
    while tokens:
        last = tokens[-1].lower().rstrip(".")
        if last in _UNIT_WORDS or re.fullmatch(r"\d+(st|nd|rd|th)", last):
            tokens.pop()
            continue
        break
    suffix_at = -1
    for index, token in enumerate(tokens):
        if token.lower().rstrip(".") in _STREET_SUFFIXES:
            suffix_at = index
    city_tokens = tokens[suffix_at + 1 :] if suffix_at >= 0 else tokens[-2:]
    city = " ".join(city_tokens).strip(" ,")
    return city if city and not re.search(r"\d", city) else ""


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame.columns = [re.sub(r"\s+", " ", str(column)).strip() for column in frame.columns]
    rename = {
        "Business Legal Name": "company",
        "Date of WARN Notice": "notice_date",
        "Impacted Site Address": "address",
        "Impacted Site County": "county",
        "Number of Affected Workers": "employees",
        "company": "company",
        "employer": "company",
        "notice_date": "notice_date",
        "notice date": "notice_date",
        "city": "city",
        "county": "county",
        "employees": "employees",
        "layoff_count": "employees",
    }
    lower_map = {column.lower(): column for column in frame.columns}
    resolved = {}
    for source, dest in rename.items():
        if source in frame.columns:
            resolved[source] = dest
        elif source.lower() in lower_map:
            resolved[lower_map[source.lower()]] = dest
    frame = frame.rename(columns=resolved)
    if "company" not in frame.columns or "notice_date" not in frame.columns:
        raise ValueError(f"NY WARN columns changed. Found {list(frame.columns)}")
    if "employees" not in frame.columns:
        raise ValueError("NY WARN file has no employee-count column.")
    if "city" not in frame.columns:
        frame["city"] = frame.get("address", pd.Series([""] * len(frame))).map(
            city_from_ny_address
        )
    if "county" not in frame.columns:
        frame["county"] = ""
    out = frame[["notice_date", "company", "city", "county", "employees"]].copy()
    out["company"] = out["company"].astype(str).str.strip()
    out["city"] = out["city"].fillna("").astype(str).str.strip()
    out["county"] = out["county"].fillna("").astype(str).str.strip()
    out = out[out["company"].ne("") & out["company"].ne("nan")]
    out["notice_date"] = pd.to_datetime(out["notice_date"], errors="coerce")
    out["employees"] = pd.to_numeric(out["employees"], errors="coerce")
    out = out.dropna(subset=["notice_date", "employees"])
    out["employees"] = out["employees"].astype(int)
    return (
        out.drop_duplicates(subset=["notice_date", "company", "city", "employees"])
        .sort_values(["notice_date", "company"], ascending=[False, True])
        .reset_index(drop=True)
    )


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


def collect_ny_warn() -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()
    print(f"Opening NY WARN dashboard: {PORTAL_URL}")
    print(f"Fetching Tableau CSV export: {TABLEAU_CSV_URL}")
    raw = _download_csv(session, TABLEAU_CSV_URL)
    if raw is not None:
        CACHE_CSV.write_text(raw.to_csv(index=False))
    elif CACHE_CSV.exists():
        print(f"  using cached {CACHE_CSV.name}")
        raw = pd.read_csv(CACHE_CSV)
    else:
        print("Tableau export failed; trying Hugging Face daily NY archive...")
        raw = _download_csv(session, HF_FALLBACK_URL)
    if raw is None:
        raise RuntimeError("Could not download New York WARN notices from Tableau or fallback.")
    cleaned = _normalize(raw)
    cleaned.to_csv(OUTPUT_CSV, index=False)
    print(f"Wrote cleaned New York data: {OUTPUT_CSV} ({len(cleaned)} rows)")
    return cleaned


def main() -> None:
    frame = collect_ny_warn()
    print(f"New York notices: {len(frame):,}")
    print(f"Workers listed: {int(frame['employees'].sum()):,}")
    print(
        f"Notice dates: {frame['notice_date'].min().date()} to "
        f"{frame['notice_date'].max().date()}"
    )


if __name__ == "__main__":
    main()
