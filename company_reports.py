"""Published car-lender and card-trust figures.

The Federal Reserve feed does not publish these company reports. This module
reads the latest filing it can parse, once a month, and keeps the previous
figure when a report cannot be read.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from datetime import datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

DATA_DIR = Path(__file__).resolve().parent / "data"
COMPANY_REPORTS_JSON = DATA_DIR / "company_reports.json"
MAX_AGE_SECONDS = 30 * 24 * 3600
USER_AGENT = "MacroPulse research (local dashboard; contact@macropulse.local)"

_MONTHS = {
    "jan": "January",
    "feb": "February",
    "mar": "March",
    "apr": "April",
    "may": "May",
    "jun": "June",
    "jul": "July",
    "aug": "August",
    "sep": "September",
    "oct": "October",
    "nov": "November",
    "dec": "December",
}


def _card(
    card_id: str,
    badge: str,
    name: str,
    figure: str,
    measure: str,
    source: str,
) -> dict:
    return {
        "id": card_id,
        "badge": badge,
        "name": name,
        "figure": figure,
        "measure": measure,
        "source": source,
    }


def fallback_reports() -> dict:
    """Last figures checked by hand. Used until a filing can be read."""
    return {
        "updated_at": None,
        "autos": [
            _card(
                "toyota",
                "TM",
                "Toyota Motor Credit",
                "0.71%",
                "Finance receivables 60 or more days late, as a share of earning assets.",
                "December 31, 2024 · Toyota Motor Credit",
            ),
            _card(
                "ford",
                "F",
                "Ford Motor Credit",
                "0.18%",
                "U.S. retail loans over 60 days late. Bankruptcies are left out of this rate.",
                "Full year 2025 · Ford Motor Credit filing",
            ),
            _card(
                "gm",
                "GM",
                "GM Financial",
                "2.9%",
                "All retail loans past due, prime and subprime together.",
                "June 30, 2025 · GM Financial, reported by S&P",
            ),
            _card(
                "honda",
                "H",
                "American Honda Finance",
                "768",
                "Average credit score on the February 2025 loan pool sold to investors.",
                "Fitch · Honda Auto Receivables 2025-1",
            ),
            _card(
                "hyundai",
                "HK",
                "Hyundai Capital",
                "773",
                "Average credit score on the 2025-D loan pool sold to investors.",
                "S&P · Hyundai Auto Receivables 2025-D",
            ),
            _card(
                "nissan",
                "N",
                "Nissan Motor Acceptance",
                "784",
                "Average credit score on the September 2026 loan pool sold to investors.",
                "Fitch · Nissan Auto Receivables 2026-B",
            ),
            _card(
                "bmw",
                "BMW",
                "BMW Group Financial Services",
                "39.4%",
                "Share of new finance contracts that were leases, worldwide.",
                "2025 · BMW Group report",
            ),
        ],
        "cards": [
            _card(
                "chase",
                "JPM",
                "JPMorgan Chase",
                "0.81%",
                "Chase card trust balances 30 or more days late.",
                "September 2026 · Chase Issuance Trust",
            ),
            _card(
                "amex",
                "AXP",
                "American Express",
                "1.2%",
                "All U.S. consumer cards 30 or more days late.",
                "September 30, 2026 · American Express filing",
            ),
            _card(
                "citi",
                "C",
                "Citigroup",
                "1.35%",
                "Citi card trust balances 30 or more days late.",
                "September 2026 · Citibank Credit Card Issuance Trust",
            ),
            _card(
                "capital_one",
                "COF",
                "Capital One",
                "1.72%",
                "Card trust receivables 30 or more days late.",
                "September 2026 · Capital One Master Trust. Discover has been part of Capital One since May 18, 2025.",
            ),
        ],
    }


def load_company_reports() -> dict:
    if not COMPANY_REPORTS_JSON.exists():
        return fallback_reports()
    try:
        loaded = json.loads(COMPANY_REPORTS_JSON.read_text())
    except (OSError, json.JSONDecodeError):
        return fallback_reports()
    fallback = fallback_reports()
    for group in ("autos", "cards"):
        by_id = {item["id"]: item for item in loaded.get(group) or [] if item.get("id")}
        loaded[group] = [by_id.get(item["id"], item) for item in fallback[group]]
    loaded.setdefault("updated_at", None)
    return loaded


def company_reports_are_fresh(max_age: float = MAX_AGE_SECONDS) -> bool:
    if not COMPANY_REPORTS_JSON.exists():
        return False
    return time.time() - COMPANY_REPORTS_JSON.stat().st_mtime <= max_age


def _fetch(url: str) -> bytes:
    from http_retry import fetch_bytes

    return fetch_bytes(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        timeout=40,
    )


def _plain(url: str) -> str:
    text = _fetch(url).decode("utf-8", "replace")
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&#160;", " ")
        .replace("&nbsp;", " ")
        .replace("&#8211;", " ")
        .replace("&#8221;", '"')
        .replace("&#58;", ":")
    )
    return re.sub(r"\s+", " ", text)


def _month_name(token: str) -> str:
    return _MONTHS.get(token[:3].lower(), token)


def _submissions(cik: str) -> dict:
    padded = str(cik).zfill(10)
    return json.loads(_fetch(f"https://data.sec.gov/submissions/CIK{padded}.json"))


def _latest_filing(cik: str, forms: set[str]) -> tuple[str, str, str]:
    recent = _submissions(cik)["filings"]["recent"]
    for form, date, accession, document in zip(
        recent["form"],
        recent["filingDate"],
        recent["accessionNumber"],
        recent["primaryDocument"],
    ):
        if form in forms:
            return date, accession, document
    raise LookupError(f"No {'/'.join(sorted(forms))} filing for {cik}")


def _archive_url(cik: str, accession: str, document: str) -> str:
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession.replace('-', '')}/{document}"
    )


def _fetch_toyota() -> dict:
    _, accession, document = _latest_filing("834071", {"10-Q", "10-K"})
    text = _plain(_archive_url("834071", accession, document))
    matched = re.search(
        r"Aggregate balances for accounts 60 or more days past due as a percentage of earning assets"
        r".*?Finance receivables\s+([0-9.]+)\s*%",
        text,
    )
    dated = re.search(
        r"September 30,\s*March 31,\s*September 30,\s*(\d{4})",
        text,
    )
    if not matched:
        raise LookupError("Toyota delinquency table was not found")
    as_of = f"September 30, {dated.group(1)}" if dated else "latest filing"
    return _card(
        "toyota",
        "TM",
        "Toyota Motor Credit",
        f"{float(matched.group(1)):.2f}%",
        "Finance receivables 60 or more days late, as a share of earning assets.",
        f"{as_of} · Toyota Motor Credit filing",
    )


def _fetch_ford() -> dict:
    _, accession, document = _latest_filing("38009", {"10-Q", "10-K"})
    text = _plain(_archive_url("38009", accession, document))
    matched = re.search(
        r"receivables at ([A-Z][a-z]+ \d{1,2}, \d{4})\. Third Quarter First Nine Months "
        r"\d{4} \d{4} \d{4} \d{4} Credit Loss Drivers "
        r"Over-60-Day delinquencies \(excl\. bankruptcies\) \(%\)\s+"
        r"([0-9.]+)\s*%\s+([0-9.]+)\s*%",
        text,
    )
    if not matched:
        raise LookupError("Ford delinquency table was not found")
    return _card(
        "ford",
        "F",
        "Ford Motor Credit",
        f"{float(matched.group(3)):.2f}%",
        "U.S. retail loans over 60 days late. Bankruptcies are left out of this rate.",
        f"{matched.group(1)} · Ford Motor Credit filing",
    )


def _fetch_gm() -> dict:
    _, accession, document = _latest_filing("804269", {"10-Q", "10-K"})
    text = _plain(_archive_url("804269", accession, document))
    matched = re.search(
        r"Finance receivables more than 30 days delinquent\s+"
        r"(?:[0-9,]+\s+){7}([0-9.]+)",
        text,
    )
    dated = re.search(r"(January|March|June|September|December) \d{1,2}, \d{4}", document)
    period = re.search(r"(\d{4})(\d{2})(\d{2})", document)
    if period:
        as_of = datetime(int(period.group(1)), int(period.group(2)), int(period.group(3))).strftime(
            "%B %-d, %Y"
        )
    else:
        as_of = "latest filing"
    if dated:
        as_of = dated.group(0)
    if not matched:
        raise LookupError("GM delinquency table was not found")
    return _card(
        "gm",
        "GM",
        "GM Financial",
        f"{float(matched.group(1)):.1f}%",
        "Retail loans more than 30 days late, prime and subprime together.",
        f"{as_of} · GM Financial filing",
    )


def _fetch_honda() -> dict:
    _, accession, document = _latest_filing("864270", {"10-Q", "10-K"})
    text = _plain(_archive_url("864270", accession, document))
    matched = re.search(
        r"((?:January|March|June|September|December) \d{1,2}, \d{4}) Retail loans: "
        r".*?Total retail loans\s+[0-9,]+\s+([0-9,]+)\s+([0-9,]+)\s+[0-9,]+\s+[0-9,]+\s+([0-9,]+)",
        text,
    )
    if not matched:
        raise LookupError("Honda aging table was not found")
    late = int(matched.group(2).replace(",", "")) + int(matched.group(3).replace(",", ""))
    total = int(matched.group(4).replace(",", ""))
    if total <= 0:
        raise LookupError("Honda retail loan total was empty")
    return _card(
        "honda",
        "H",
        "American Honda Finance",
        f"{late / total * 100:.2f}%",
        "Retail loans 60 or more days late.",
        f"{matched.group(1)} · American Honda Finance filing",
    )


def _fetch_citi() -> dict:
    html = _fetch(
        "https://www.citigroup.com/global/investors/fixed-income-investor-relations/"
        "credit-card-securitization/trust-data"
    ).decode("utf-8", "replace")
    row = re.search(r"Delinquencies 30\+ Days</th>(.*?)</tr>", html, re.I | re.S)
    if not row:
        raise LookupError("Citi delinquency row was not found")
    cells = re.findall(r">(\d+\.\d+%)<", row.group(1))
    headers = re.findall(
        r">((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2})<",
        html[max(0, row.start() - 2500) : row.start()],
    )
    if len(cells) < 2 or not headers:
        raise LookupError("Citi delinquency columns were not found")
    month, year = headers[0].split()
    return _card(
        "citi",
        "C",
        "Citigroup",
        cells[1],
        "Citi card trust balances 30 or more days late.",
        f"{_month_name(month)} 20{year} · Citibank Credit Card Issuance Trust",
    )


def _fetch_chase() -> dict:
    today = datetime.now()
    year, month = today.year, today.month
    pdf = None
    label = ""
    for _ in range(4):
        stamp = f"{year}-{month:02d}"
        url = (
            "https://www.jpmorganchase.com/content/dam/jpmc/jpmorgan-chase-and-co/"
            "investor-relations/documents/asset-back-securities/chase-issuance-trust/"
            f"chait-historical-trust-{stamp}.pdf"
        )
        try:
            pdf = _fetch(url)
            label = stamp
            break
        except Exception:
            month -= 1
            if month == 0:
                month = 12
                year -= 1
    if pdf is None:
        raise LookupError("Chase trust report was not found")
    text = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages
    )
    months = re.match(r"([A-Za-z]{3}-\d{2})", text.strip())
    total = re.search(r"Delinquency:\s*.*?Total\s+([0-9.]+)%", text, re.S)
    if not months or not total:
        raise LookupError("Chase delinquency line was not found")
    month_token, year_token = months.group(1).split("-")
    return _card(
        "chase",
        "JPM",
        "JPMorgan Chase",
        f"{float(total.group(1)):.2f}%",
        "Chase card trust balances 30 or more days late.",
        f"{_month_name(month_token)} 20{year_token} · Chase Issuance Trust",
    )


def _fetch_amex() -> dict:
    recent = _submissions("4962")["filings"]["recent"]
    checked = 0
    for form, accession, document in zip(
        recent["form"], recent["accessionNumber"], recent["primaryDocument"]
    ):
        if form != "8-K":
            continue
        checked += 1
        if checked > 8:
            break
        text = _plain(_archive_url("4962", accession, document))
        matched = re.search(
            r"U\.S\. Consumer Card balances\s*:.*?30 days past due as a % of total\s+([0-9.]+)\s*%",
            text,
        )
        dated = re.search(
            r"months ended ([A-Z][a-z]+) 30, [A-Z][a-z]+ \d{1,2} and [A-Z][a-z]+ \d{1,2}, (\d{4})",
            text,
        )
        if matched and dated:
            return _card(
                "amex",
                "AXP",
                "American Express",
                f"{float(matched.group(1)):.1f}%",
                "All U.S. consumer cards 30 or more days late.",
                f"{dated.group(1)} 30, {dated.group(2)} · American Express filing",
            )
        time.sleep(0.15)
    raise LookupError("American Express delinquency filing was not found")


def _fetch_capital_one() -> dict:
    query = urllib.parse.urlencode(
        {
            "q": '"Delinquencies 30 + Days as a Percent of End of the Month Total Receivables"',
            "forms": "10-D",
            "entityName": "Capital One Master Trust",
            "dateRange": "custom",
            "startdt": "2025-01-01",
            "enddt": datetime.now().strftime("%Y-%m-%d"),
        }
    )
    payload = json.loads(_fetch(f"https://efts.sec.gov/LATEST/search-index?{query}"))
    hits = payload.get("hits", {}).get("hits") or []
    if not hits:
        raise LookupError("Capital One trust filing was not found")
    hits.sort(key=lambda hit: hit["_source"].get("period_ending") or "", reverse=True)
    filing_id = hits[0]["_id"]
    accession, document = filing_id.split(":", 1)
    text = _plain(_archive_url("1163321", accession, document))
    matched = re.search(
        r"MONTHLY PERIOD:\s*([A-Z][a-z]+ \d{4}).*?"
        r"Delinquencies 30 \+ Days as a Percent of End of the Month Total Receivables\s+([0-9.]+)\s*%",
        text,
    )
    if not matched:
        raise LookupError("Capital One delinquency line was not found")
    return _card(
        "capital_one",
        "COF",
        "Capital One",
        f"{float(matched.group(2)):.2f}%",
        "Card trust receivables 30 or more days late.",
        f"{matched.group(1)} · Capital One Master Trust. Discover has been part of Capital One since May 18, 2025.",
    )


_FETCHERS = (
    _fetch_toyota,
    _fetch_ford,
    _fetch_gm,
    _fetch_honda,
    _fetch_citi,
    _fetch_chase,
    _fetch_amex,
    _fetch_capital_one,
)


def refresh_company_reports(force: bool = False) -> dict:
    if not force and company_reports_are_fresh():
        return load_company_reports()
    current = load_company_reports()
    by_id = {
        item["id"]: item
        for group in ("autos", "cards")
        for item in current[group]
    }
    for fetcher in _FETCHERS:
        try:
            updated = fetcher()
        except Exception as exc:
            print(f"Company report unchanged ({fetcher.__name__}): {exc}")
            continue
        if updated and updated.get("figure"):
            by_id[updated["id"]] = updated
        time.sleep(0.15)
    fallback = fallback_reports()
    payload = {
        "updated_at": datetime.now().strftime("%Y-%m-%d"),
        "autos": [by_id[item["id"]] for item in fallback["autos"]],
        "cards": [by_id[item["id"]] for item in fallback["cards"]],
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = COMPANY_REPORTS_JSON.with_name(COMPANY_REPORTS_JSON.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2))
    os.replace(temporary, COMPANY_REPORTS_JSON)
    print(f"Wrote {COMPANY_REPORTS_JSON}")
    return payload


if __name__ == "__main__":
    reports = refresh_company_reports(force=True)
    for group in ("autos", "cards"):
        for item in reports[group]:
            print(f"{item['id']}: {item['figure']} | {item['source']}")
