"""Public headlines that touch jobs, cars, household bills, or the wider economy.

Each item is kept only when the wording matches that part of the site.
The toll is a reading of those words, not a forecast.
"""

from __future__ import annotations

import email.utils
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import quote

import requests

from http_retry import mount_retries

_US_SCOPE = (
    '("United States" OR "U.S." OR America OR American OR "Federal Reserve" OR Fed '
    'OR Treasury OR Congress OR "Wall Street" OR "White House" OR California OR Texas '
    'OR Florida OR "New York")'
)
_QUERIES = {
    "Economy": f'(economy OR recession OR "Federal Reserve" OR inflation OR GDP) {_US_SCOPE} when:2d',
    "Jobs": f'(layoffs OR "job cuts" OR unemployment OR hiring OR payrolls OR "jobless claims") {_US_SCOPE} when:2d',
    "Cars": f'("auto loan" OR "car sales" OR "vehicle sales" OR repossession OR dealership OR automaker) {_US_SCOPE} when:2d',
    "Bills": f'(mortgage OR "home prices" OR rent OR "credit card" OR foreclosure OR "household debt") {_US_SCOPE} when:2d',
    "War": '("United States" OR "U.S." OR Pentagon OR NATO) (war OR invasion OR airstrike OR "military strike" OR missiles OR troops) when:2d',
}

_TOPIC_WORDS = {
    "Economy": (
        "economy", "recession", "federal reserve", "inflation", "gdp",
        "interest rate", "rates", "treasury", "consumer spending", "downturn",
        "soft landing", "growth",
    ),
    "Jobs": (
        "layoff", "layoffs", "job cut", "job cuts", "unemployment", "hiring",
        "payroll", "jobless", "furlough", "jobs", "workers",
    ),
    "Cars": (
        "car", "cars", "auto", "vehicle", "dealership", "repossession",
        "automaker", "dealer",
    ),
    "Bills": (
        "mortgage", "rent", "home price", "housing", "credit card",
        "foreclosure", "household", "grocery", "utility", "bills", "savings",
    ),
    "War": (
        "war", "invasion", "invade", "military", "missile", "troops",
        "strike", "airstrike", "pentagon", "nato", "navy", "soldier",
    ),
}

_STRAIN = (
    "layoff", "layoffs", "job cut", "job cuts", "furlough", "hiring freeze",
    "recession", "downturn", "contraction", "default", "delinquen",
    "repossess", "foreclos", "evict", "rate hike", "rates rise", "rates climb",
    "higher rates", "inflation rise", "inflation jump", "prices surge",
    "prices soar", "bankrupt", "missed payment", "late payment",
    "falling behind", "sales slump", "sales drop", "sales fall", "sales plunge",
    "unemployment rise", "unemployment rose", "unemployment jump",
    "jobless claims rise", "jobless claims rose", "mortgage rates rise",
    "mortgage rates climb", "rent hike", "rents rise", "rents soar",
    "credit crunch", "shutdown", "insolvency",
)

_RELIEF = (
    "rate cut", "rates fall", "rates ease", "rates drop", "lower rates",
    "jobs added", "payrolls rise", "payrolls beat", "job growth", "hiring boom",
    "inflation cool", "inflation ease", "inflation slow", "prices fall",
    "prices ease", "mortgage rates fall", "mortgage rates ease",
    "mortgage rates drop", "rent relief", "rents ease", "rents fall",
    "unemployment fall", "unemployment drop", "jobless claims fall",
    "jobless claims drop", "sales rebound", "sales rise", "sales jump",
    "record sales", "soft landing", "delinquencies fall", "late payments fall",
)

_SKIP = (
    "detained", "arrested", "arrest", "police", "shooting", "killed",
    "flames", "engulfed", "firefighter", "suspect", "confrontation",
    "inmate", "pleads guilty", "breaking into", "stolen",
    "commercial real estate", "lawyers weekly", "stray dog",
    "better buy", "price target", "stock to buy", "shares of",
)
_COMMERCIAL = ("office campus", "office tower", "office building", "hotel")
_FOREIGN = (
    "australia", "australian", "aussie", "rba", "albanese", "chalmers",
    "sydney", "melbourne", "canberra", "reserve bank",
    "britain", "british", "uk", "england", "scotland", "wales", "london",
    "starmer", "sunak", "bank of england", "boe",
    "europe", "european", "eurozone", "euro area", "ecb", "lagarde", "brussels",
    "germany", "german", "berlin", "france", "french", "paris",
    "italy", "italian", "spain", "spanish", "netherlands", "dutch",
    "ireland", "irish", "greece", "greek", "portugal", "sweden", "norway",
    "switzerland", "swiss",
    "canada", "canadian", "toronto", "ottawa", "vancouver", "trudeau",
    "bank of canada",
    "china", "chinese", "beijing", "shanghai", "pboc",
    "india", "indian", "delhi", "mumbai", "modi", "reserve bank of india",
    "japan", "japanese", "tokyo", "boj", "bank of japan",
    "mexico", "mexican", "brazil", "brazilian", "korea", "korean", "seoul",
    "russia", "russian", "moscow", "south africa",
    "israel", "israeli", "iran", "iranian", "gaza",
    "poland", "polish", "ukraine", "ukrainian", "houthi", "riyadh", "saudi",
    "yemen", "taiwan", "taipei", "syria", "syrian", "iraq", "iraqi",
    "afghanistan", "pakistan",
)
_FOREIGN_SOURCES = (
    "sydney morning herald", "the age", "the australian", "financial review",
    "abc australia", "bbc", "times of india", "globe and mail", "toronto star",
    "japan times", "south china morning post", "deutsche welle", "le monde",
    "the hindu", "nz herald", "guardian australia", "sky news australia",
)
_US = (
    "u.s.", "united states", "america", "american", "federal reserve",
    "treasury", "congress", "white house", "wall street", "california", "texas",
    "florida", "new york", "pentagon",
)
_IMPROVING = (
    "lessen", "eases", "ease", "fades", "fade", "improve", "improves",
    "cooling", "cools", "slows", "slowing", "retreats", "recedes",
)
_TOLL_RANK = {"strain": 0, "relief": 1, "mixed": 2, "watch": 3}
_TOLL_LABEL = {
    "strain": "Adds strain",
    "relief": "Eases pressure",
    "mixed": "Mixed",
    "watch": "Worth a look",
}


def _feed_url(query: str) -> str:
    return (
        "https://news.google.com/rss/search?q="
        + quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def _fetch(url: str) -> str:
    session = mount_retries(requests.Session(), total=2, backoff=0.4)
    response = session.get(
        url,
        timeout=8,
        headers={"User-Agent": "Mozilla/5.0 (compatible; MacroPulse/1.0)"},
    )
    response.raise_for_status()
    return response.text


def _published(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _when(stamp: datetime | None) -> str:
    if stamp is None:
        return ""
    now = datetime.now(timezone.utc)
    days = (now.date() - stamp.date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    return stamp.strftime("%b ") + str(stamp.day)


def _hits(text: str, phrases: Iterable[str]) -> int:
    count = 0
    for phrase in phrases:
        if phrase in text:
            count += 1
    return count


def _toll(title: str) -> str:
    text = title.lower()
    strain = _hits(text, _STRAIN)
    relief = _hits(text, _RELIEF)
    if "hiring freeze" in text or "job cut" in text or "layoff" in text:
        relief = 0
    if strain and _hits(text, _IMPROVING) and "plunge" not in text and "soar" not in text:
        relief += 1
        strain = max(0, strain - 1)
    if strain and relief:
        if abs(strain - relief) <= 1:
            return "mixed"
        return "strain" if strain > relief else "relief"
    if strain:
        return "strain"
    if relief:
        return "relief"
    return "watch"


def _has_term(text: str, phrases: Iterable[str]) -> bool:
    padded = f" {text.lower()} "
    for phrase in phrases:
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", padded):
            return True
    return False


def _mentions_country(text: str) -> bool:
    if re.search(r"(?<![A-Za-z])U\.?S\.?(?![A-Za-z])", text):
        return True
    return _has_term(text, _US)


def _is_us(title: str, source: str = "") -> bool:
    """Keep a story about the United States. A foreign dateline without one is out."""
    if _mentions_country(title) or _mentions_country(source):
        return True
    blob = f"{title} {source}"
    if _has_term(blob, _FOREIGN) or any(name in blob.lower() for name in _FOREIGN_SOURCES):
        return False
    return True


_WAR_WORDS = (
    "war", "invasion", "invade", "military", "missile", "troops",
    "strike", "airstrike", "pentagon", "nato", "navy", "soldier",
)
_WAR_NOISE = (
    "better buy", "price target", "stock to buy", "fantasy", "nfl", "nba",
    "premier league", "world series",
)


def _skip_war(title: str) -> bool:
    text = title.lower()
    if any(phrase in text for phrase in _WAR_NOISE):
        return True
    return not any(word in text for word in _WAR_WORDS)


def _war_toll(title: str) -> str:
    text = title.lower()
    strain = _hits(text, (
        "invade", "invasion", "airstrike", "missile", "missiles", "mobiliz",
        "troops", "warship", "bomb", "nuclear", "attack", "strike",
    ))
    relief = _hits(text, (
        "ceasefire", "truce", "peace talk", "peace deal", "withdraw",
        "de-escalat", "talks resume", "stand down",
    ))
    if strain and relief:
        return "mixed" if abs(strain - relief) <= 1 else ("strain" if strain > relief else "relief")
    if strain:
        return "strain"
    if relief:
        return "relief"
    return "watch"


def _skip(title: str, topic: str, source: str = "") -> bool:
    if not _is_us(title, source):
        return True
    if topic == "War":
        return _skip_war(title)
    text = title.lower()
    if any(phrase in text for phrase in _SKIP):
        return True
    if topic == "Bills" and any(phrase in text for phrase in _COMMERCIAL):
        if not any(word in text for word in ("rent", "mortgage", "home", "household", "credit card")):
            return True
    if title.count("–") + title.count(" - ") >= 2 and len(title.split()) < 8:
        return True
    return False


def _matches(topic: str, title: str) -> bool:
    text = title.lower()
    return any(word in text for word in _TOPIC_WORDS[topic])


def _clean_title(title: str, source: str) -> str:
    trimmed = re.sub(r"\s+", " ", title).strip()
    suffix = f" - {source}".lower()
    if source and trimmed.lower().endswith(suffix):
        trimmed = trimmed[: -len(suffix)].strip()
    return trimmed


def _parse(topic: str, xml_text: str) -> list[dict]:
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    channel = root.find("channel")
    if channel is None:
        return []
    rows = []
    seen: set[str] = set()
    for item in channel.findall("item"):
        raw_title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not raw_title or not link:
            continue
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        title = _clean_title(raw_title, source)
        key = re.sub(r"[^a-z0-9]+", "", title.lower())
        if not title or key in seen or _skip(title, topic, source) or not _matches(topic, title):
            continue
        stamp = _published(item.findtext("pubDate") or "")
        if stamp is not None and (datetime.now(timezone.utc) - stamp).total_seconds() > 3 * 86400:
            continue
        seen.add(key)
        toll = _war_toll(title) if topic == "War" else _toll(title)
        rows.append(
            {
                "title": title,
                "link": link,
                "source": source or "News",
                "when": _when(stamp),
                "stamp": stamp.timestamp() if stamp else 0,
                "toll": toll,
                "toll_label": _TOLL_LABEL[toll],
            }
        )
    rows.sort(key=lambda row: (_TOLL_RANK[row["toll"]], -row["stamp"]))
    return rows[:4]


def fetch_all_headlines() -> dict[str, list[dict]]:
    """Download each section feed. A failed feed comes back empty."""
    urls = {topic: _feed_url(query) for topic, query in _QUERIES.items()}

    def _one(topic: str) -> tuple[str, list[dict]]:
        try:
            return topic, _parse(topic, _fetch(urls[topic]))
        except Exception:
            return topic, []

    found = {topic: [] for topic in _QUERIES}
    with ThreadPoolExecutor(max_workers=5) as pool:
        for topic, rows in pool.map(_one, _QUERIES):
            found[topic] = rows
    return found
