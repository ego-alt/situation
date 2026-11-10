"""Quanta Magazine, the second section of Dispatch: its articles since the last issue came out.

Read from its feed as each issue is published. The feed carries the last five or so, about ten
days' worth, which covers the week before an issue; nothing is kept but the issue.
"""
from datetime import UTC, datetime, timedelta

import feedparser
import requests

from papers import clean
from sources import USER_AGENT

FEED = "https://www.quantamagazine.org/feed/"
NAME = "Quanta Magazine"
TIMEOUT = 20


def read() -> list:
    resp = requests.get(FEED, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return feedparser.parse(resp.content).entries


def articles(entries: list, end: datetime) -> list[dict]:
    """The articles out in the week before `end`, newest first, shaped like papers.section's so
    the page sets them the same way."""
    found = []
    for e in entries:
        when = datetime(*e.published_parsed[:6], tzinfo=UTC) if e.get("published_parsed") else None
        if not when or not end - timedelta(days=7) <= when < end:
            continue
        # The feed's summary is the standfirst, then a "first appeared on Quanta" line.
        standfirst = clean((e.get("summary") or "").split("<p>The post ")[0])
        image = next((m["url"] for m in e.get("media_content") or [] if m.get("url", "").startswith("https://")), None)
        found.append({"doi": e.link, "journal": (e.get("tags") or [{}])[0].get("term") or NAME, "via": NAME,
                      "title": clean(e.title), "authors": [a["name"] for a in e.get("authors") or [] if a.get("name")],
                      "abstract": standfirst, "published": when.date().isoformat(), "url": e.link,
                      "figure": image, "figure_large": image, "free": None, "cover": False, "commentary": []})
    return sorted(found, key=lambda a: a["published"], reverse=True)
