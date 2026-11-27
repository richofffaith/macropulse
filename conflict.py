"""Public readings related to a U.S. war.

The news-heat number is the Caldara and Iacoviello geopolitical risk index.
The percentages are live Polymarket prices on specific questions.
Neither one is the true chance that the United States goes to war.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import requests
import xlrd

from http_retry import mount_retries

GPR_URL = "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"
GPR_PAGE = "https://www.matteoiacoviello.com/gpr.htm"
MARKET_SEARCH = "https://gamma-api.polymarket.com/public-search"
MANIFOLD_SEARCH = "https://api.manifold.markets/v0/search-markets"
MANIFOLD_QUERIES = ("US Iran war", "invade Taiwan", "NATO article 5", "US military strike")
REGISTER_SEARCH = "https://www.federalregister.gov/api/v1/documents.json"
MARKET_QUERIES = (
    "U.S. invade",
    "US strike",
    "invade Taiwan",
    "U.S. nuclear test",
    "NATO article 5",
    "US military action",
)

_DROP = re.compile(
    r"\d+\s+countries|\bor more\b|ceasefire|home value|jobs added|unemployment|"
    r"fed decision|president|dissent",
    re.I,
)


def _session() -> requests.Session:
    return mount_retries(requests.Session(), total=2, backoff=0.4)


def _get(url: str, **params: Any) -> requests.Response:
    target = url if not params else url + "?" + urlencode(params)
    response = _session().get(
        target,
        timeout=20,
        headers={"User-Agent": "Mozilla/5.0 (compatible; MacroPulse/1.0)"},
    )
    response.raise_for_status()
    return response


def _heat(week: float, percentile: float) -> tuple[str, str]:
    if week >= 180 or percentile >= 90:
        return "stress", "Hot"
    if week >= 120 or percentile >= 60:
        return "caution", "Elevated"
    return "healthy", "Quieter"


def _gpr() -> dict | None:
    response = _get(GPR_URL)
    book = xlrd.open_workbook(file_contents=response.content)
    sheet = book.sheet_by_index(0)
    headers = [str(sheet.cell_value(0, col)).strip() for col in range(sheet.ncols)]
    index = {name: pos for pos, name in enumerate(headers)}
    needed = ("date", "GPRD", "GPRD_ACT", "GPRD_THREAT", "GPRD_MA30", "GPRD_MA7")
    if any(name not in index for name in needed):
        return None
    rows = []
    for row in range(1, sheet.nrows):
        try:
            week = float(sheet.cell_value(row, index["GPRD_MA7"]))
            day = float(sheet.cell_value(row, index["GPRD"]))
        except (TypeError, ValueError):
            continue
        if week != week or day != day:
            continue
        serial = sheet.cell_value(row, index["date"])
        try:
            stamp = xlrd.xldate_as_datetime(serial, book.datemode)
        except Exception:
            stamp = None
        rows.append(
            {
                "stamp": stamp,
                "day": day,
                "week": week,
                "month": float(sheet.cell_value(row, index["GPRD_MA30"])),
                "acts": float(sheet.cell_value(row, index["GPRD_ACT"])),
                "threat": float(sheet.cell_value(row, index["GPRD_THREAT"])),
            }
        )
    if not rows:
        return None
    latest = rows[-1]
    below = sum(1 for row in rows if row["week"] < latest["week"])
    percentile = 100.0 * below / len(rows)
    level, label = _heat(latest["week"], percentile)
    stamp = latest["stamp"]
    as_of = stamp.strftime("%b ") + str(stamp.day) + ", " + str(stamp.year) if isinstance(stamp, datetime) else ""
    trail = [round(row["week"], 2) for row in rows[-180:]]
    return {
        "as_of": as_of,
        "day": latest["day"],
        "week": latest["week"],
        "month": latest["month"],
        "acts": latest["acts"],
        "threat": latest["threat"],
        "percentile": percentile,
        "level": level,
        "label": label,
        "trail": trail,
        "source": GPR_PAGE,
    }


def _kind(question: str) -> str:
    text = question.lower()
    if "taiwan" in text:
        return "Taiwan"
    if "nuclear" in text:
        return "Nuclear"
    if "invade" in text or "invasion" in text:
        return "Invasions"
    if "strike" in text:
        return "Strikes"
    return "Other"


def _mentions_us(text: str) -> bool:
    if "u.s." in text or "united states" in text:
        return True
    return re.search(r"(?<![a-z])us(?![a-z])", text) is not None


def _keep_market(question: str) -> bool:
    text = question.lower()
    if _DROP.search(text):
        return False
    if _mentions_us(text) and any(
        word in text
        for word in ("invade", "invasion", "strike", "strikes", "nuclear", "go to war", "at war")
    ):
        return True
    if "china" in text and "invade" in text and "taiwan" in text:
        return True
    if "nato" in text and "article 5" in text:
        return True
    return False


def _prices(raw: Any) -> list[float]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list) or not raw:
        return []
    try:
        return [float(item) for item in raw]
    except (TypeError, ValueError):
        return []


def _markets() -> list[dict]:
    found: dict[str, dict] = {}
    for query in MARKET_QUERIES:
        try:
            payload = _get(
                MARKET_SEARCH,
                q=query,
                events_status="active",
                limit_per_type=6,
                search_profiles="false",
                search_tags="false",
            ).json()
        except Exception:
            continue
        for event in payload.get("events") or []:
            slug = str(event.get("slug") or "")
            for market in event.get("markets") or []:
                if market.get("closed"):
                    continue
                question = str(market.get("question") or "").strip()
                if not question or not _keep_market(question):
                    continue
                prices = _prices(market.get("outcomePrices"))
                if not prices:
                    continue
                yes = prices[0]
                if yes < 0 or yes > 1:
                    continue
                try:
                    volume = float(market.get("volume") or 0)
                except (TypeError, ValueError):
                    volume = 0.0
                if volume < 1000:
                    continue
                key = re.sub(r"[^a-z0-9]+", "", question.lower())
                row = {
                    "question": question,
                    "yes": yes,
                    "volume": volume,
                    "kind": _kind(question),
                    "url": "https://polymarket.com/event/" + slug if slug else "https://polymarket.com",
                }
                current = found.get(key)
                if current is None or volume > current["volume"]:
                    found[key] = row
    rows = list(found.values())
    rows.sort(key=lambda row: row["volume"], reverse=True)
    return rows[:12]


_MONTHS = {
    "January": 1,
    "February": 2,
    "March": 3,
    "April": 4,
    "May": 5,
    "June": 6,
    "July": 7,
    "August": 8,
    "September": 9,
    "October": 10,
    "November": 11,
    "December": 12,
}


def _iso_date(month_name: str, day: str, year: str) -> str:
    month = _MONTHS.get(month_name)
    if not month:
        return ""
    return f"{int(year):04d}-{month:02d}-{int(day):02d}"


def _keep_guess(question: str) -> bool:
    text = question.lower()
    if "culture war" in text or "senator" in text or "representative" in text:
        return False
    if "how many" in text and any(word in text for word in ("kill", "die", "dead")):
        return False
    if "taiwan" in text and "invade" in text:
        return True
    if "nato" in text and "article 5" in text:
        return True
    if _mentions_us(text) and any(word in text for word in ("war", "invade", "invasion", "strike", "iran")):
        return True
    return False


def _guesses() -> list[dict]:
    """Manifold play-money prices. A different crowd from the cash bets."""
    found: dict[str, dict] = {}
    for query in MANIFOLD_QUERIES:
        try:
            payload = _get(MANIFOLD_SEARCH, term=query, limit=8, filter="open").json()
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        for market in payload:
            if market.get("isResolved") or market.get("outcomeType") != "BINARY":
                continue
            question = str(market.get("question") or "").strip()
            if not question or not _keep_guess(question):
                continue
            try:
                yes = float(market.get("probability"))
                volume = float(market.get("volume") or 0)
            except (TypeError, ValueError):
                continue
            if yes <= 0.01 or yes >= 0.99 or volume < 400:
                continue
            key = re.sub(r"[^a-z0-9]+", "", question.lower())
            row = {
                "question": question,
                "yes": yes,
                "volume": volume,
                "url": str(market.get("url") or "https://manifold.markets"),
            }
            current = found.get(key)
            if current is None or volume > current["volume"]:
                found[key] = row
    rows = list(found.values())
    rows.sort(key=lambda row: row["volume"], reverse=True)
    return rows[:5]


def _sale_text(url: str) -> str:
    try:
        return _get(url).text
    except Exception:
        return ""


def _sales() -> list[dict]:
    """Unclassified arms-sale notices the Pentagon published for Congress."""
    since = (datetime.utcnow().date() - timedelta(days=45)).isoformat()
    try:
        payload = _get(
            REGISTER_SEARCH,
            per_page=6,
            order="newest",
            **{
                "conditions[term]": '"Arms Sales Notification"',
                "conditions[publication_date][gte]": since,
            },
        ).json()
    except Exception:
        return []
    notices = []
    for doc in payload.get("results") or []:
        published = str(doc.get("publication_date") or "")
        number = str(doc.get("document_number") or "")
        if not published or not number:
            continue
        text_url = "https://www.federalregister.gov/documents/full_text/text/{}/{}.txt".format(
            published.replace("-", "/"),
            number,
        )
        text = re.sub(r"\s+", " ", _sale_text(text_url))
        buyer_match = re.search(r"Prospective Purchaser:\s*(.+?)\s+\(ii\)", text)
        money_match = re.search(r"TOTAL[\s\.]*\$?\s*([\d,.]+)\s*(billion|million)", text, re.I)
        if not buyer_match or not money_match:
            continue
        buyer = re.sub(r"^Government of\s+", "", buyer_match.group(1).strip())
        buyer = re.sub(r"\s+", " ", buyer)
        amount = "$" + money_match.group(1) + " " + money_match.group(2).lower()
        issued = re.search(r"Dated:\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})", text)
        stamp = _iso_date(*issued.groups()) if issued else ""
        if not stamp:
            stamp = published
        notices.append(
            {
                "buyer": buyer[:90],
                "amount": amount,
                "date": stamp,
                "url": str(doc.get("html_url") or "https://www.federalregister.gov"),
            }
        )
    return notices[:5]


def war_snapshot() -> dict:
    """News heat, cash prices, arms notices, and play-money guesses."""
    gpr = None
    markets: list[dict] = []
    guesses: list[dict] = []
    sales: list[dict] = []
    try:
        gpr = _gpr()
    except Exception:
        gpr = None
    try:
        markets = _markets()
    except Exception:
        markets = []
    try:
        guesses = _guesses()
    except Exception:
        guesses = []
    try:
        sales = _sales()
    except Exception:
        sales = []
    return {"gpr": gpr, "markets": markets, "guesses": guesses, "sales": sales}
