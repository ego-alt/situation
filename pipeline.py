"""One fetch: store new articles, re-group the last two days, score, tier.

Every run re-clusters the whole window, which keeps grouping simple and
consistent; a story keeps its id when most of its articles are the same as
last time. A story whose articles have all left the window is closed, not
deleted, so it keeps its id for the weekly view and the notifications."""
from datetime import UTC, datetime, timedelta

from cluster import cluster
from geo import locate, places, spots
from models import Article, DayTally, ItnSnapshot, SourceStatus, Story, db, story_articles
from score import BACKGROUND, MAJOR, assess, match_itn

WINDOW = timedelta(hours=48)
ITN_FRESH = timedelta(hours=24)     # how long an "In the news" match makes a story FLASH
SHOWN = timedelta(hours=24)         # what the page counts as today
MAX_SHOWN = 10                      # a backstop: at most this many stories on screen
SILENT_AFTER = timedelta(hours=6)   # a feed with nothing for this long is flagged


def clock() -> datetime:
    """Now, in UTC (the tests freeze it)."""
    return datetime.now(UTC)


def store(items: list[dict]) -> int:
    """Insert new articles; refresh feed position for ones seen before."""
    known = {a.url: a for a in Article.query.filter(Article.url.in_([i["url"] for i in items])).all()}
    added = 0
    for item in items:
        published = item.get("published")
        if published is not None and published.tzinfo is not None:
            published = published.astimezone(UTC).replace(tzinfo=None)
        row = known.get(item["url"])
        if row is None:
            db.session.add(Article(
                url=item["url"], source=item["source"], outlet=item.get("outlet") or item["source"],
                title=item["title"], summary=item.get("summary"), published=published,
                position=item.get("position"), spots=article_spots(item)))
            added += 1
        else:
            row.position = item.get("position")
    db.session.commit()
    return added


def article_spots(item) -> list[dict]:
    """The cities and regions an article names, worked out once when it's stored."""
    title, summary = (item["title"], item.get("summary")) if isinstance(item, dict) else (item.title, item.summary)
    return spots(f"{title}. {summary or ''}")


def regroup(now: datetime | None = None, itn: dict | None = None) -> list[Story]:
    now = now or clock()
    since = (now - WINDOW).replace(tzinfo=None)
    articles = Article.query.filter(db.or_(Article.published >= since,
                                           db.and_(Article.published.is_(None), Article.fetched >= since))).all()
    for a in articles:
        if a.spots is None:            # stored before places were tracked
            a.spots = article_spots(a)
    items = [a.as_item() for a in articles]
    by_id = {a.id: a for a in articles}
    groups = [[items[i] for i in g] for g in cluster(items)]
    itn = itn if itn is not None else latest_itn()
    matched = match_itn(groups, itn.get("items", []))

    first_seen = itn_first_seen()
    old = {s.id: {a.id for a in s.articles} for s in Story.query.filter_by(closed=False)}
    claimed, keep = set(), []
    for k, group in enumerate(groups):
        ids = {a["id"] for a in group}
        best = max(((len(ids & arts), sid) for sid, arts in old.items() if sid not in claimed), default=(0, None))
        story = db.session.get(Story, best[1]) if best[0] * 2 >= len(ids) else None
        if story is None:
            story = Story(headline="", tier="background", first_seen=now.replace(tzinfo=None))
            db.session.add(story)
        else:
            claimed.add(story.id)
        place = locate(group)
        item = matched.get(k)
        fresh = item is not None and now - first_seen.get(item, now) <= ITN_FRESH
        a = assess(group, place, item, now, itn_fresh=fresh)
        story.headline, story.tier, story.score = a["headline"], a["tier"], a["score"]
        story.outlets, story.leads, story.itn = a["outlets"], a["leads"], a["itn"]
        story.newest = a["newest"].astimezone(UTC).replace(tzinfo=None)
        if place:
            story.iso, story.place, story.lat, story.lon = place["iso"], place["name"], place["lat"], place["lon"]
            story.x, story.y = place["x"], place["y"]
        story.articles = [by_id[i["id"]] for i in group]
        keep.append(story)
    for sid in set(old) - claimed:
        story = db.session.get(Story, sid)
        if story.tier == BACKGROUND:
            db.session.delete(story)        # never shown: nothing to remember
        else:
            story.closed = True
    db.session.commit()
    return keep


def latest_itn() -> dict:
    snap = ItnSnapshot.query.order_by(ItnSnapshot.fetched.desc()).first()
    return {"items": snap.items, "ongoing": snap.ongoing} if snap else {"items": [], "ongoing": []}


def itn_first_seen() -> dict[str, datetime]:
    """When each "In the news" item first appeared (UTC)."""
    seen: dict[str, datetime] = {}
    for snap in ItnSnapshot.query.order_by(ItnSnapshot.fetched):
        for item in snap.items:
            seen.setdefault(item, snap.fetched.replace(tzinfo=UTC))
    return seen


def save_itn(itn: dict, now: datetime | None = None):
    """Keep a copy of "In the news" when it changes."""
    if not itn.get("items"):
        return
    last = latest_itn()
    if last["items"] != itn["items"] or last["ongoing"] != itn.get("ongoing", []):
        fetched = (now or clock()).astimezone(UTC).replace(tzinfo=None)
        db.session.add(ItnSnapshot(items=itn["items"], ongoing=itn.get("ongoing", []), fetched=fetched))
        db.session.commit()


def record_sources(report: dict, now: datetime | None = None):
    """{source key: (item count, error or None)} from one fetch."""
    now = (now or clock()).astimezone(UTC).replace(tzinfo=None)
    for key, (count, error) in report.items():
        row = db.session.get(SourceStatus, key) or SourceStatus(key=key)
        row.last_try, row.items, row.error = now, count, error
        if count:
            row.last_ok = now
        db.session.add(row)
    db.session.commit()


def source_health(sources, now: datetime | None = None) -> dict:
    """{"ok": n, "total": n, "silent": [names]}: feeds with nothing for 6 hours.
    A feed that hasn't been tried yet isn't counted against it."""
    now = (now or clock()).astimezone(UTC).replace(tzinfo=None)
    rows = {r.key: r for r in SourceStatus.query.all()}
    silent = []
    for src in sources:
        row = rows.get(src.key)
        if row is None or row.last_try is None:
            continue
        if row.last_ok is None or now - row.last_ok > SILENT_AFTER:
            silent.append(src.name)
    return {"ok": len(sources) - len(silent), "total": len(sources), "silent": silent}


def last_fetch_ok() -> datetime | None:
    """The last time any feed returned items (UTC)."""
    when = db.session.query(db.func.max(SourceStatus.last_ok)).scalar()
    return when.replace(tzinfo=UTC) if when else None


def on_screen(now: datetime | None = None) -> tuple[list[Story], int]:
    """Today's stories at Headline or above, best first, capped at MAX_SHOWN;
    and how many cleared the bar before the cap."""
    now = now or clock()
    since = (now - SHOWN).replace(tzinfo=None)
    cleared = (Story.query.filter(Story.tier != BACKGROUND, Story.closed.is_(False), Story.newest >= since)
               .order_by(Story.score.desc()).all())
    return cleared[:MAX_SHOWN], len(cleared)


def tally(now: datetime | None = None):
    """Remember the most stories shown at once today, to tune the bar later."""
    now = now or clock()
    shown, cleared = on_screen(now)
    row = db.session.get(DayTally, now.date()) or DayTally(day=now.date(), major=0, headline=0, cleared=0)
    row.major = max(row.major, sum(s.tier == MAJOR for s in shown))
    row.headline = max(row.headline, sum(s.tier != MAJOR for s in shown))
    row.cleared = max(row.cleared, cleared)
    db.session.add(row)
    db.session.commit()


def prune(days: int = 30):
    cutoff = (clock() - timedelta(days=days)).replace(tzinfo=None)
    Article.query.filter(Article.fetched < cutoff).delete()
    ItnSnapshot.query.filter(ItnSnapshot.fetched < cutoff).delete()
    Story.query.filter(Story.closed.is_(True), Story.newest < cutoff).delete()
    # SQLite doesn't cascade without foreign keys on; drop the dangling links.
    dangling = (story_articles.c.article_id.not_in(db.select(Article.id))
                | story_articles.c.story_id.not_in(db.select(Story.id)))
    db.session.execute(story_articles.delete().where(dangling))
    db.session.commit()


TRACK = timedelta(days=7)


def tracks(areas: list[set[str]], now: datetime | None = None, limit: int = 40) -> list[list[dict]]:
    """For each set of countries, where its reporting has been over the past
    week: every city or region named there, how many articles named it and
    when last. One pass over the week's articles for all of them."""
    since = ((now or clock()) - TRACK).replace(tzinfo=None)
    seen: list[dict[tuple, dict]] = [{} for _ in areas]
    for a in Article.query.filter(Article.published >= since, Article.spots.isnot(None)):
        when = a.published.replace(tzinfo=UTC)
        for s in a.spots:
            for isos, found in zip(areas, seen, strict=True):
                if s["iso"] in isos:
                    spot = found.setdefault((s["name"], s["iso"]), {**s, "n": 0, "last": when})
                    spot["n"] += 1
                    spot["last"] = max(spot["last"], when)
    return [sorted(found.values(), key=lambda s: -s["n"])[:limit] for found in seen]


def ongoing_situations(stories: list[Story], itn: dict, now: datetime | None = None) -> list[dict]:
    """Wikipedia's "Ongoing" topics, each with its latest development today:
    what most outlets carry. A single outlet's piece isn't quoted (it's as
    often an essay as news), only counted."""
    since = ((now or clock()) - SHOWN).replace(tzinfo=None)
    out = []
    for topic in itn.get("ongoing", []):
        isos = set(places(topic.replace("Russo-Ukrainian", "Russian Ukrainian")))
        here = [s for s in stories if s.iso in isos and not s.closed and s.newest and s.newest >= since]
        shared = sorted((s for s in here if s.outlets >= 2), key=lambda s: -s.score)
        out.append({"topic": topic, "isos": sorted(isos), "stories": shared[:4],
                    "latest": shared[0] if shared else None, "singles": len(here) - len(shared)})
    return out


def run(fetch_items, fetch_itn, now: datetime | None = None) -> dict:
    """fetch_items() → feed items; fetch_itn() → {"items", "ongoing"}."""
    items = fetch_items()
    added = store(items)
    itn = fetch_itn()
    save_itn(itn, now)
    stories = regroup(now, itn if itn.get("items") else None)
    tally(now)
    return {"fetched": len(items), "new": added, "stories": len(stories),
            "shown": sum(1 for s in stories if s.tier != "background")}
