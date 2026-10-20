"""Fetching: outlet RSS feeds and Wikipedia's "In the news"."""
import html
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import feedparser
import requests

from sources import ITN_URL, SOURCES, USER_AGENT, Source

_TAG = re.compile(r"<[^>]+>")


def _text(raw: str | None, limit: int = 400) -> str:
    text = html.unescape(_TAG.sub(" ", raw or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _when(entry, *keys) -> datetime | None:
    for key in keys:
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=UTC)
    return None


def web_link(url: str | None) -> str | None:
    """The link if it's an ordinary web address; a feed could carry a
    javascript: link, which would run in the page when tapped."""
    url = (url or "").strip()
    return url if re.match(r"https?://", url, re.I) else None


def fetch_source(source: Source, timeout: int = 20) -> list[dict]:
    """A feed's items, in the feed's own order. Raises when the feed can't be read."""
    resp = requests.get(source.url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    for position, entry in enumerate(feed.entries):
        title = _text(entry.get("title"), 300)
        url = web_link(entry.get("link"))
        if not (title and url):
            continue
        items.append({
            "source": source.key, "outlet": source.outlet, "title": title, "url": url,
            "summary": _text(entry.get("summary") or entry.get("description")),
            "published": _when(entry, "published_parsed", "updated_parsed"),
            "updated": _when(entry, "updated_parsed"), "position": position,
        })
    return items


def _try(source: Source) -> tuple[list[dict], str | None]:
    try:
        return fetch_source(source), None
    except requests.RequestException as e:
        response = getattr(e, "response", None)
        return [], f"HTTP {response.status_code}" if response is not None else type(e).__name__


def fetch_all(sources=SOURCES, report: dict | None = None) -> list[dict]:
    """Every feed's items; an article carried by two feeds of one outlet once.
    `report`, if given, gets {source key: (item count, error or None)}."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_try, sources))
    if report is not None:
        report.update({s.key: (len(items), err) for s, (items, err) in zip(sources, results, strict=True)})
    batches = [items for items, _ in results]
    seen, out = set(), []
    for item in (i for batch in batches for i in batch):
        if item["url"] not in seen:
            seen.add(item["url"])
            out.append(item)
    return out


def parse_itn(page_html: str) -> dict:
    """The "In the news" template → {"items": [...], "ongoing": [...]}.

    The template is a bulleted list of the current items, then a line of
    ongoing topics, then recent deaths. Items are full sentences; ongoing
    topics are short names ("Iran war")."""
    blocks = re.split(r"<div[^>]*(?:itn-footer|hlist)[^>]*>", page_html, maxsplit=1)
    head = blocks[0]
    items = [_text(li, 500) for li in re.findall(r"<li>(.*?)</li>", head, re.S)]
    # Drop the photo notes: "(pictured)", "(damage pictured)", "(Smith pictured)".
    items = [re.sub(r"\s*\([^()]*\bpictured\)", "", i) for i in items if len(i) > 25]
    ongoing = []
    m = re.search(r"Ongoing:?\s*</b>(.*?)(?:Recent deaths|</div>)", page_html, re.S | re.I)
    if m:
        ongoing = [_text(li, 120) for li in re.findall(r"<li>(.*?)</li>", m.group(1), re.S)]
        # A topic can carry a "timeline" link beside it; that's not part of its name.
        ongoing = [re.sub(r"\s+timeline$", "", o, flags=re.I) for o in ongoing if o and o.lower() != "timeline"]
    return {"items": items, "ongoing": ongoing}


def fetch_itn(timeout: int = 20) -> dict:
    try:
        resp = requests.get(ITN_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        resp.raise_for_status()
        return parse_itn(resp.json()["parse"]["text"])
    except (requests.RequestException, KeyError, ValueError):
        return {"items": [], "ongoing": []}
