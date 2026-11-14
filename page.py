"""The page's data: today's stories with their sources and places, the long-running situations,
the disaster signals and how the feeds are doing.

It's made up once, after each fetch, and kept as the text it's served as (models.Page): the
news doesn't change between fetches, so the page's polls cost a read, and nothing at all when
the page already has it (the ETag).
"""
import json
import threading
from datetime import datetime, timedelta

import geo
import ongoing
import pipeline
import signals
from models import Page, Story, db
from projection import project
from score import clean_title, is_headline
from sources import BY_KEY
from text import tokens

DEVELOPMENTS = 5                # at most this many of a running story's events listed
LATEST = timedelta(hours=6)     # how recent a story's latest development is to be said
SPOTS = 8                       # at most this many places on a story


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


# ---------- made up, kept and served ----------

def build(now: datetime) -> dict:
    """The page as it stands at `now`."""
    shown, cleared = pipeline.on_screen(now)
    itn = pipeline.latest_itn()
    return {
        "fetched": _iso(pipeline.last_fetch_ok()),
        "stories": [_story(s, now) for s in shown],
        "cleared": cleared,
        "ongoing": [{"topic": o["topic"], "isos": o["isos"], "story_ids": [s.id for s in o["stories"]],
                     "latest": o["latest"].headline if o["latest"] else None, "singles": o["singles"],
                     "track": [_spot(t, n=t["n"], last=_iso(t["last"])) for t in o["track"]]}
                    for o in ongoing.follow(itn["ongoing"], now)],
        "itn": itn["items"],
        "signals": _signals(now, shown),
        "sources": pipeline.source_health(now),
        "counts": {"major": sum(s.tier == "major" for s in shown),
                   "headline": sum(s.tier == "headline" for s in shown),
                   "watched": Story.query.filter_by(closed=False).count()},
    }


_making = threading.Lock()      # one at a time: a reader before the first fetch is done makes it up as the fetch does


def publish(now: datetime) -> str:
    """Make the page up and keep it, as its JSON text."""
    with _making:
        row = db.session.get(Page, 1) or Page(id=1)
        row.made, row.body = now, json.dumps(build(now), ensure_ascii=False, separators=(",", ":"))
        db.session.add(row)
        db.session.commit()
        return row.body


def published() -> str:
    """The page as last made up (now, the first time)."""
    row = db.session.get(Page, 1)
    return row.body if row else publish(pipeline.clock())


def brief(page: dict, limit: int = 5) -> str:
    """The morning brief: the page's top headlines as lines of text."""
    top = page["stories"][:limit]
    if not top:
        return "Quiet day: nothing at headline level in the last 24 hours."
    return "\n".join(f"{'▲' if s['tier'] == 'major' else '·'} {s['headline']}" for s in top)


def country(iso: str, now: datetime) -> dict | None:
    """A country's card: its capital, leader and time zone, and when it was last in the news here."""
    card = geo.country(iso)
    if card is None:
        return None
    shown = Story.query.filter(Story.iso == card["iso"], Story.tier != "background")
    last = shown.order_by(Story.newest.desc()).first()
    return {**card, "stories_30d": shown.filter(Story.newest >= now - timedelta(days=30)).count(),
            "last": {"headline": last.headline, "at": _iso(last.newest), "id": last.id} if last else None}


# ---------- a story ----------

def _story(s: Story, now: datetime) -> dict:
    seen = set()
    sources = []
    # Newest first, one article per outlet (its latest).
    for a in sorted(s.articles, key=lambda a: a.published or a.fetched or datetime.min, reverse=True):
        if a.outlet in seen:
            continue
        seen.add(a.outlet)
        src = BY_KEY.get(a.source)
        # Updated only when it's meaningfully after it went out (feeds often stamp both alike).
        revised = a.updated if a.updated and a.published and (a.updated - a.published).total_seconds() > 600 else None
        sources.append({"outlet": src.name.split(" Hong Kong")[0] if src else a.outlet,
                        "title": clean_title(a.title), "url": a.url,
                        "published": _iso(a.published or a.fetched), "updated": _iso(revised)})
    spots = _story_spots(s)
    out = {"id": s.id, "headline": s.headline, "tier": s.tier, "score": s.score, "outlets": s.outlets,
           "leads": s.leads, "itn": s.itn, "iso": s.iso, "place": s.place, **_xy(s),
           "newest": _iso(s.newest), "first_seen": _iso(s.first_seen), "sources": sources,
           "spots": spots, "zone": _zone(s, spots), "latest": _latest(s, sources, now)}
    if s.running:
        out |= _developments(s)
    return out


def _xy(s: Story) -> dict:
    """Where a story goes on the map, from its place (the stored x and y wait for the next regroup)."""
    if s.lat is None or s.lon is None:
        return {"x": s.x, "y": s.y}
    x, y = project(s.lon, s.lat)
    return {"x": x, "y": y}


def _spot(s: dict, **extra) -> dict:
    x, y = project(s["lon"], s["lat"])
    return {"name": s["name"], "iso": s["iso"], "kind": s["kind"], "x": x, "y": y,
            "lat": s["lat"], "lon": s["lon"], **extra}


def _story_spots(s: Story) -> list[dict]:
    """The cities and regions a story's articles name, most named first: those in
    its own country, and elsewhere only what two or more of its articles name
    (one article's aside about another country isn't where the story is)."""
    counts: dict[tuple, list] = {}
    for a in s.articles:
        for spot in a.spots or []:
            counts.setdefault((spot["name"], spot["iso"]), [spot, 0])[1] += 1
    people = geo.surnames(f"{a.title}. {a.summary or ''}" for a in s.articles)   # "Mike Rogers": a bare "Rogers" is him
    counts = {k: c for k, c in counts.items() if (k[1] == s.iso or c[1] >= 2) and k[0] not in people}
    ranked = sorted(counts.values(), key=lambda c: -c[1])[:SPOTS]
    return [_spot(spot, n=n) for spot, n in ranked]


def _zone(s: Story, spots: list[dict]) -> str | None:
    """The time zone where it's happening: its first place in its own country, else the country's."""
    here = next((p for p in spots if p["iso"] == s.iso), None)
    return geo.timezone(s.iso, here["lat"], here["lon"]) if here else geo.timezone(s.iso)


def _develops(title: str, headline: str) -> bool:
    """A title that moves the story on: it shares some of the headline's words (not
    another story chained in) but not most of them (not the headline reworded)."""
    a, b = set(tokens(title)), set(tokens(headline))
    return bool(a and b) and 0 < len(a & b) / min(len(a), len(b)) < 0.5


def _latest(s: Story, sources: list[dict], now: datetime) -> dict | None:
    """The newest development, when it's recent and says something the headline
    doesn't: how a long-running story has moved on."""
    for src in sources:                       # newest first
        if not src["published"] or datetime.fromisoformat(src["published"]) < now - LATEST:
            return None
        if is_headline(src["title"]) and _develops(src["title"], s.headline):
            return {"title": src["title"], "outlet": src["outlet"], "at": src["published"], "url": src["url"]}
    return None


def _developments(s: Story) -> dict:
    """A running story's events, the most carried first: its developments."""
    events = Story.query.filter(Story.run_id == s.id).order_by(Story.outlets.desc(), Story.newest.desc()).all()
    # a headline that says what happened (not a live blog); single-outlet reports
    # fill in after those carried by more (a race call is often one wire's)
    shown = [e for e in events if is_headline(e.headline)][:DEVELOPMENTS]
    return {"running": True, "events": len(events),
            "developments": [{"id": e.id, "headline": e.headline, "outlets": e.outlets, "newest": _iso(e.newest)}
                             for e in shown]}


# ---------- the signals ----------

def _signals(now: datetime, shown: list[Story]) -> list[dict]:
    """The disaster alerts and strong earthquakes happening now (signals.py), each with the
    story about it when the news has one, and, for an earthquake or eruption, how long
    before the first report it was seen."""
    out = []
    for g in signals.current(now):
        story = next((s for s in shown if s.iso == g.iso and signals.about(g, " ".join(
            [s.headline, *(a.title for a in s.articles)]))), None)
        first = min((a.published for a in story.articles if a.published), default=None) if story else None
        lead = round((first - g.started).total_seconds() / 60) if first and g.kind in ("EQ", "VO") else None
        x, y = project(g.lon, g.lat)
        out.append({"id": g.id, "source": g.source, "kind": g.kind, "level": g.level, "title": g.title,
                    "place": g.place, "iso": g.iso, "x": x, "y": y, "magnitude": g.magnitude,
                    "started": _iso(g.started), "ended": _iso(g.ended), "url": g.url,
                    "story_id": story.id if story else None, "lead": lead if lead and lead > 0 else None})
    return out
