"""One fetch: store new articles, re-group the last two days, score, tier.

Every run re-clusters the whole window, which keeps grouping simple and
consistent; a story keeps its id when most of its articles are the same as
last time. A story whose articles have all left the window is closed, not
deleted, so it keeps its id for the weekly view and the notifications."""
from datetime import UTC, datetime, timedelta

import situations
from cluster import cluster
from geo import locate, places, spots
from models import Article, DayTally, ItnSnapshot, SourceStatus, Story, db, story_articles
from news import is_news, news_only
from score import BACKGROUND, HEADLINE, MAJOR, assess, match_itn, need, rank_features, weights

WINDOW = timedelta(hours=48)
ITN_FRESH = timedelta(hours=24)     # how long an "In the news" match makes a story FLASH
SHOWN = timedelta(hours=24)         # what the page counts as today
MAX_SHOWN = 10                      # a backstop: at most this many stories on screen
SILENT_AFTER = timedelta(hours=6)   # a feed with nothing for this long is flagged


def clock() -> datetime:
    """Now, in UTC (the tests freeze it)."""
    return datetime.now(UTC)


def store(items: list[dict]) -> int:
    """Insert new articles; for ones seen before, refresh their feed position
    and when they were last updated."""
    known = {a.url: a for a in Article.query.filter(Article.url.in_([i["url"] for i in items])).all()}
    added = 0
    for item in items:
        def naive(t):
            return t.astimezone(UTC).replace(tzinfo=None) if t is not None and t.tzinfo is not None else t
        published, updated = naive(item.get("published")), naive(item.get("updated"))
        row = known.get(item["url"])
        if row is None:
            db.session.add(Article(
                url=item["url"], source=item["source"], outlet=item.get("outlet") or item["source"],
                title=item["title"], summary=item.get("summary"), published=published, updated=updated,
                position=item.get("position"), spots=article_spots(item)))
            added += 1
        else:
            row.position = item.get("position")
            if updated and (row.updated is None or updated > row.updated):
                row.updated = updated
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
    every = [a.as_item() for a in articles]
    items = news_only(every)     # opinion, explainers etc. aren't grouped or shown
    # A running story's words (situations.py), found before grouping, so that
    # within one an article the learned filter doubts still counts when the
    # stricter rules call it news: "US midterms: Democrat wins North Carolina"
    # is how running stories are headlined.
    day_start = now - situations.DAY
    topics = situations.topics([a for a in every if a["published"] >= day_start], *usual_words(now))
    if topics:
        kept = {a["id"] for a in items}
        items += [a for a in every if a["id"] not in kept and is_news(a)
                  and any(situations.matches(situations.words(a), t) for t in topics)]
    by_id = {a.id: a for a in articles}
    itn = itn if itn is not None else latest_itn()
    groups = [[items[i] for i in g] for g in cluster(items)]
    matched = match_itn(groups, itn.get("items", []))

    first_seen = itn_first_seen()
    today = now - SHOWN
    active = len({i["outlet"] for i in items if i["published"] >= today})
    old = {s.id: {a.id for a in s.articles} for s in Story.query.filter_by(closed=False, running=False)}
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
        a = assess(group, place, item, now, itn_fresh=fresh, active=active)
        story.headline, story.tier, story.score = a["headline"], a["tier"], a["score"]
        story.outlets, story.leads, story.itn = a["outlets"], a["leads"], a["itn"]
        story.newest = a["newest"].astimezone(UTC).replace(tzinfo=None)
        if place:
            story.iso, story.place, story.lat, story.lon = place["iso"], place["name"], place["lat"], place["lon"]
            story.x, story.y = place["x"], place["y"]
        story.articles = [by_id[i["id"]] for i in group]
        story.run_id = None                 # (set again below if it's part of a running story)
        keep.append(story)
    for sid in set(old) - claimed:
        story = db.session.get(Story, sid)
        if story.tier == BACKGROUND:
            db.session.delete(story)        # never shown: nothing to remember
        else:
            story.closed = True
    db.session.flush()
    runs = regroup_running(groups, keep, topics, by_id, now, active)
    db.session.commit()
    return keep + runs


def regroup_running(groups: list[list[dict]], events: list[Story], topics: list[dict], by_id: dict,
                    now: datetime, active: int) -> list[Story]:
    """Running stories among the events (situations.py): each kept as a story
    of its own over all its events' articles, scored and tiered like any
    story, named for the phrase its headlines use; its events point to it, and
    the page shows it in their place. A running story keeps its id while most
    of its articles do."""
    found = situations.find(groups, topics)
    old = {s.id: {a.id for a in s.articles} for s in Story.query.filter_by(closed=False, running=True)}
    claimed, out = set(), []
    for f in found:
        union = [a for k in f["events"] for a in groups[k]]
        ids = {a["id"] for a in union}
        best = max(((len(ids & arts), sid) for sid, arts in old.items() if sid not in claimed), default=(0, None))
        story = db.session.get(Story, best[1]) if best[0] * 2 >= len(ids) else None
        if story is None:
            story = Story(headline="", tier=BACKGROUND, running=True, first_seen=now.replace(tzinfo=None))
            db.session.add(story)
        else:
            claimed.add(story.id)
        place = locate(union)
        a = assess(union, place, None, now, active=active)
        tier, score = running_tier(a, [groups[k] for k in f["events"]], now, active)
        story.headline, story.tier, story.score = f["name"], tier, score
        story.outlets, story.leads, story.itn = a["outlets"], a["leads"], None
        story.newest = a["newest"].astimezone(UTC).replace(tzinfo=None)
        if place:
            story.iso, story.place, story.lat, story.lon = place["iso"], place["name"], place["lat"], place["lon"]
            story.x, story.y = place["x"], place["y"]
        story.articles = [by_id[i] for i in ids]
        db.session.flush()
        for k in f["events"]:
            events[k].run_id = story.id
        out.append(story)
    for sid in set(old) - claimed:
        story = db.session.get(Story, sid)
        if story.tier == BACKGROUND:
            db.session.delete(story)
        else:
            story.closed = True
    return out


# A running story is FLASH while it's breaking: carried by this many outlets, with
# this many of its developments carried by two or more outlets in the last RECENT.
RUNNING_FLASH_OUTLETS = 8
RUNNING_FLASH_DEVELOPMENTS = 3
RECENT = timedelta(hours=12)


def running_tier(a: dict, events: list[list[dict]], now: datetime, active: int) -> tuple[str, float]:
    """A running story's tier and score. It's as fresh as its newest developments
    (it began days ago, but it's news while it's moving), and FLASH while several
    of them are breaking at once, though the world desks rarely lead with a
    country's own election night."""
    def outlets(g):
        return len({x.get("outlet") or x["source"] for x in g})
    moving = [g for g in events if outlets(g) >= 2 and now - max(x["published"] for x in g) <= RECENT]
    parts = dict(a["parts"], freshness=1.0 if moving else a["parts"]["freshness"])
    score = round(sum(w * x for w, x in zip(weights().values(), rank_features(parts), strict=True)), 2)
    tier = a["tier"]
    if (tier != BACKGROUND and len(moving) >= RUNNING_FLASH_DEVELOPMENTS
            and a["outlets"] >= need(RUNNING_FLASH_OUTLETS, active)):
        tier = MAJOR
    elif tier == BACKGROUND and len(moving) >= RUNNING_FLASH_DEVELOPMENTS:
        tier = HEADLINE
    return tier, score


_usual: dict = {}


def usual_words(now: datetime):
    """How the fortnight before the last day used each word (situations.baseline),
    worked out once an hour: it barely moves between fetches."""
    end = (now - situations.DAY).replace(minute=0, second=0, microsecond=0)
    if end not in _usual:
        start = end - timedelta(days=situations.BASELINE_DAYS)
        rows = (db.session.query(Article.title, Article.summary, Article.published, Article.fetched)
                .filter(Article.published >= start.replace(tzinfo=None), Article.published < end.replace(tzinfo=None)))
        _usual.clear()
        _usual[end] = situations.baseline([{"title": t, "summary": s, "published": (p or f).replace(tzinfo=UTC)}
                                           for t, s, p, f in rows])
    return _usual[end]


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
    # An event that's part of a running story is shown inside it, not on its own.
    cleared = (Story.query.filter(Story.tier != BACKGROUND, Story.closed.is_(False), Story.newest >= since,
                                  Story.run_id.is_(None))
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
