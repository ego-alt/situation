"""Features: what an issue carries from outside the journals, written to be read whole.

One piece a day from each place, read from its feed as the issue is made up (take): NASA Earth
Observatory's image of the day with its story (public domain). Each is a story like a paper's
(papers.story), its whole text in place of an abstract.
"""
import html
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import feedparser
import requests

from models import SourceStatus
from papers import clean, get, paragraphs, plain, story

FRESH = timedelta(days=4)       # a piece out longer ago than this has had its chance (a weekend and a missed day)


def _earth(e) -> dict | None:
    """An image of the day: the page's paragraphs down to its downloads, the last of them the
    credit, which names who wrote it."""
    if not any(t.term == "Earth Observatory" for t in e.get("tags", [])):
        return None
    body = re.split(r"<h2[^>]*>\s*Downloads", e.content[0].value)[0]
    *text, credit = re.findall(r"<p class=\"wp-block-paragraph\">(.*?)</p>", body, re.S) or [""]
    image = re.search(r"<img[^>]+src=\"([^\"?]+)", body)
    by = re.search(r"(?:Story|Caption|Text) by ([^.<]+)", plain(credit))
    figure = html.unescape(image.group(1)) if image else None
    return {"abstract": paragraphs(text), "authors": [by.group(1).strip()] if by else [],
            "figure": figure and f"{figure}?w=640&fit=clip", "figure_large": figure and f"{figure}?w=1280&fit=clip"}


@dataclass(frozen=True)
class Feature:
    key: str
    name: str
    feed: str
    read: Callable      # a feed's entry → what its story has besides a title, a link and a day, or None if it isn't one


FEATURES = [
    Feature("earth", "NASA Earth Observatory", "https://earthobservatory.nasa.gov/feeds/image-of-the-day.rss", _earth),
]


def pieces(f: Feature) -> list[dict]:
    """Everything in a place's feed, as stories, the oldest first."""
    # NASA's feed runs its namespaces together (…"xmlns:…), which no parser forgives.
    feed = feedparser.parse(re.sub(rb'"(?=xmlns:)', b'" ', get(f.feed).content))
    out = []
    for e in feed.entries:
        more = e.get("content") and e.get("published_parsed") and f.read(e)
        if more and more["abstract"]:
            day = datetime(*e.published_parsed[:6], tzinfo=UTC).date()
            out.append(story(e.link, f.name, clean(e.title), e.link, day, **more))
    return sorted(out, key=lambda s: s["published"])


def take(now: datetime, carried: set[str]) -> list[dict]:
    """A piece from each place for the issue made up at `now`: the oldest of the last FRESH
    days that no issue in `carried` (their stories' ids) has had."""
    since = (now - FRESH).date().isoformat()
    taken, report = [], {}
    for f in FEATURES:
        try:
            found = pieces(f)
            report[f"features:{f.key}"] = (len(found), None)
        except (requests.RequestException, ValueError, KeyError, AttributeError, IndexError) as e:
            found, report[f"features:{f.key}"] = [], (0, type(e).__name__)
        taken += [s for s in found if s["published"] >= since and s["id"] not in carried][:1]
    SourceStatus.record(report, now)
    return taken
