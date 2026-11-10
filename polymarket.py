"""Polymarket, read only (its public API; nothing is traded): the markets Dispatch asks its
weekly question from (forecast.py) and lists as coming up (dispatch.py), and what's never shown.
"""
import json
import re
from datetime import UTC, datetime

import requests

from sources import USER_AGENT

EVENTS = "https://gamma-api.polymarket.com/events"
MARKET = "https://gamma-api.polymarket.com/markets/"
MIN_VOLUME = 25_000                 # dollars traded, so the odds mean something
TIMEOUT = 20

# The categories read, and the subject each counts as (first match).
CATEGORIES = ["politics", "elections", "world", "geopolitics", "economy", "fed", "finance", "business",
              "tech", "ai", "science", "climate", "weather", "commodities", "oil"]
SUBJECTS = [
    ("elections", {"elections", "global-elections", "us-election", "midterms", "primaries"}),
    ("commodities", {"commodities", "oil", "gold", "silver", "natural-gas"}),
    ("economics", {"economy", "fed", "fed-rates", "finance", "business", "inflation", "gdp", "jobs", "tariffs",
                   "stocks", "earnings", "macro-indicators"}),
    ("science & tech", {"tech", "ai", "science", "space"}),
    ("climate", {"climate", "climate-science", "weather", "hurricanes"}),
    ("geopolitics", {"geopolitics", "world", "foreign-affairs", "international-affairs"}),
    ("politics", {"politics"}),
]

# What's never shown: subjects you don't want, and markets that are coin flips, not questions.
BLOCK_TAGS = {"sports", "esports", "crypto", "crypto-prices", "bitcoin", "ethereum", "solana", "memecoins",
              "pop-culture", "culture", "entertainment", "games", "awards", "movies", "music", "celebrities",
              "mentions", "tweet-markets", "elon-tweets", "up-or-down", "combos", "parlays", "midterm-combos"}
BLOCK_WORDS = re.compile(
    r"\b(die[sd]?|death|dead|kill\w*|assassinat\w*|attack\w*|strikes?|struck|bomb\w*|shoot\w*|invade[sd]?|"
    r"invasion|missiles?|drones?|casualt\w*|hostages?|war|ceasefire|ukmto|incidents?|"
    r"up or down|temperature|precipitation|tweets?|posts|truth social|say|mentions?|combo)\b", re.I)


# ---------- reading ----------

def get(url, **params):
    resp = requests.get(url, params=params or None, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp


def read_events(start: datetime, end: datetime) -> list[dict]:
    """Open events with a market closing in the window, from every category read."""
    seen = {}
    for slug in CATEGORIES:
        for offset in range(0, 2000, 100):
            batch = get(EVENTS, tag_slug=slug, closed="false", end_date_min=start.isoformat(),
                        end_date_max=end.isoformat(), limit=100, offset=offset).json()
            seen.update((e["id"], e) for e in batch)
            if len(batch) < 100:
                break
    return list(seen.values())


def read_market(market: str) -> dict:
    return get(MARKET + market).json()


def time(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.replace(" ", "T").replace("Z", "+00:00")
    if re.search(r"[+-]\d\d$", text):
        text += ":00"
    return datetime.fromisoformat(text).astimezone(UTC)


def yes(market: dict) -> float | None:
    """The price of Yes: the crowd's chance."""
    prices = json.loads(market.get("outcomePrices") or "[]")
    return float(prices[0]) if prices else None


def outcome(market: dict) -> str | None:
    """What a settled market came to: yes, no or void (refunded at 50/50); None until settled."""
    if not market.get("closed") or market.get("umaResolutionStatus") != "resolved":
        return None
    price = yes(market)
    return None if price is None else "yes" if price > 0.99 else "no" if price < 0.01 else "void"


# ---------- what's shown ----------

def subject(event: dict) -> str | None:
    """The event's subject, or None when it's blocked or in none of the subjects read."""
    tags = {t.get("slug", "").lower() for t in event.get("tags") or []}
    if tags & BLOCK_TAGS or BLOCK_WORDS.search(event.get("title") or ""):
        return None
    return next((name for name, slugs in SUBJECTS if tags & slugs), None)
