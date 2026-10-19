"""One fetch: store new articles, re-group the last two days, score, tier.

Every run re-clusters the whole window, which keeps grouping simple and
consistent; a story keeps its id when most of its articles are the same as
last time."""
from datetime import UTC, datetime, timedelta

from cluster import cluster
from geo import locate, places
from models import Article, ItnSnapshot, Story, db, utcnow
from score import assess, match_itn

WINDOW = timedelta(hours=48)


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
                position=item.get("position")))
            added += 1
        else:
            row.position = item.get("position")
    db.session.commit()
    return added


def regroup(now: datetime | None = None, itn: dict | None = None) -> list[Story]:
    now = now or datetime.now(UTC)
    since = (now - WINDOW).replace(tzinfo=None)
    articles = Article.query.filter(db.or_(Article.published >= since,
                                           db.and_(Article.published.is_(None), Article.fetched >= since))).all()
    items = [a.as_item() for a in articles]
    by_id = {a.id: a for a in articles}
    groups = [[items[i] for i in g] for g in cluster(items)]
    itn = itn if itn is not None else latest_itn()
    matched = match_itn(groups, itn.get("items", []))

    old = {s.id: {a.id for a in s.articles} for s in Story.query.all()}
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
        a = assess(group, place, matched.get(k), now)
        story.headline, story.tier, story.score = a["headline"], a["tier"], a["score"]
        story.outlets, story.leads, story.itn = a["outlets"], a["leads"], a["itn"]
        story.newest = a["newest"].astimezone(UTC).replace(tzinfo=None)
        if place:
            story.iso, story.place, story.lat, story.lon = place["iso"], place["name"], place["lat"], place["lon"]
            story.x, story.y = place["x"], place["y"]
        story.articles = [by_id[i["id"]] for i in group]
        keep.append(story)
    for sid in set(old) - claimed:
        db.session.delete(db.session.get(Story, sid))
    db.session.commit()
    return keep


def latest_itn() -> dict:
    snap = ItnSnapshot.query.order_by(ItnSnapshot.fetched.desc()).first()
    return {"items": snap.items, "ongoing": snap.ongoing} if snap else {"items": [], "ongoing": []}


def save_itn(itn: dict):
    if itn.get("items"):
        db.session.add(ItnSnapshot(items=itn["items"], ongoing=itn.get("ongoing", [])))
        db.session.commit()


def prune(days: int = 30):
    cutoff = utcnow() - timedelta(days=days)
    Article.query.filter(Article.fetched < cutoff).delete()
    ItnSnapshot.query.filter(ItnSnapshot.fetched < cutoff).delete()
    db.session.commit()


def ongoing_situations(stories: list[Story], itn: dict) -> list[dict]:
    """Wikipedia's "Ongoing" topics, each with the stories it covers."""
    out = []
    for topic in itn.get("ongoing", []):
        isos = set(places(topic.replace("Russo-Ukrainian", "Russian Ukrainian")))
        here = [s for s in stories if s.iso in isos]
        # Its latest development: what most outlets are carrying, not a lone essay.
        # (none when nothing has two outlets yet).
        related = sorted((s for s in here if s.outlets >= 2), key=lambda s: -s.score)
        out.append({"topic": topic, "isos": sorted(isos), "stories": related[:4]})
    return out


def run(fetch_items, fetch_itn, now: datetime | None = None) -> dict:
    """fetch_items() → feed items; fetch_itn() → {"items", "ongoing"}."""
    items = fetch_items()
    added = store(items)
    itn = fetch_itn()
    save_itn(itn)
    stories = regroup(now, itn if itn.get("items") else None)
    return {"fetched": len(items), "new": added, "stories": len(stories),
            "shown": sum(1 for s in stories if s.tier != "background")}
