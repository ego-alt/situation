"""Your judgements on /label: what only you can say, which stories matter.

  rank   two stories: which matters more? Close calls among the stories near
         the bar (scores within a factor of RANK_RATIO).
  show   one story: would you want it on your page today? Rankings set the
         order; this says where the bar goes.

Each question is asked about a saved snapshot of the articles (instance/
snapshots/), so `flask evaluate` can score any version of the ranking on
exactly what you judged, and `flask train` can fit the rank model on it. The
newest HOLDOUT_DAYS days of labels are held out: tune on the older ones, and
believe a change only if the held-out numbers agree.

(Which articles are one event isn't a matter of taste: Claude labels it, in
groundtruth.py.)
"""
import gzip
import json
import pathlib
import random
import re
from datetime import UTC, datetime, timedelta

from flask import current_app

import cluster as clustering
from models import Article, Label, db, utcnow
from sources import BY_KEY

RANK_ANSWERS = {"a", "b", "equal"}
SHOW_ANSWERS = {"yes", "no", "borderline"}
MIX = {"rank": 0.7, "show": 0.3}     # how often each question comes up
RANK_RATIO = 2.0
RANK_POOL = 40                # stories near the bar that questions draw from
SNAPSHOT_EVERY = timedelta(hours=6)
HOLDOUT_DAYS = 2
_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{4}$")
_cache: dict[str, dict] = {}


# Snapshots ------------------------------------------------------------------

def snapshots_dir() -> pathlib.Path:
    default = pathlib.Path(current_app.instance_path) / "snapshots"
    path = pathlib.Path(current_app.config.get("SNAPSHOTS_DIR") or default)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_snapshot(now: datetime) -> str:
    """The articles the app is grouping now, as the fetcher returns them."""
    from pipeline import WINDOW, latest_itn
    since = (now - WINDOW).replace(tzinfo=None)
    articles = Article.query.filter(db.or_(Article.published >= since,
                                           db.and_(Article.published.is_(None), Article.fetched >= since))).all()
    items = [{"source": a.source, "title": a.title, "url": a.url, "summary": a.summary, "position": a.position,
              "published": (a.published or a.fetched).replace(tzinfo=UTC).isoformat()} for a in articles]
    name = now.strftime("%Y-%m-%dT%H%M")
    data = {"taken": now.isoformat(), "items": items, "itn": latest_itn()}
    with gzip.open(snapshots_dir() / f"{name}.json.gz", "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return name


def current_snapshot(now: datetime) -> str:
    """The newest snapshot, or a new one if that is over SNAPSHOT_EVERY old."""
    names = sorted(p.name.removesuffix(".json.gz") for p in snapshots_dir().glob("*.json.gz"))
    if names:
        taken = datetime.strptime(names[-1], "%Y-%m-%dT%H%M").replace(tzinfo=UTC)
        if now - taken < SNAPSHOT_EVERY:
            return names[-1]
    return save_snapshot(now)


def read_snapshot(data: dict) -> tuple[list[dict], dict, datetime]:
    """Items deduplicated, with outlets and times, as the pipeline sees them."""
    seen, items = set(), []
    for i in data["items"]:
        if i["url"] in seen or i["source"] not in BY_KEY:
            continue
        seen.add(i["url"])
        items.append({**i, "outlet": BY_KEY[i["source"]].outlet,
                      "published": datetime.fromisoformat(i["published"]) if i["published"] else None})
    return items, data["itn"], datetime.fromisoformat(data["taken"])


def load_snapshot(name: str) -> tuple[list[dict], dict, datetime]:
    if not _NAME.match(name or ""):
        raise ValueError("not a snapshot name")
    with gzip.open(snapshots_dir() / f"{name}.json.gz", "rt", encoding="utf-8") as f:
        return read_snapshot(json.load(f))


def assessed(items: list[dict], itn: dict, now: datetime) -> list[tuple[list[dict], dict]]:
    """Each story with its assessment, as pipeline.regroup makes them. Without
    the history of when an "In the news" item first appeared, every match
    counts as fresh."""
    from geo import locate
    from pipeline import SHOWN
    from score import assess, match_itn, news_only
    items = news_only(items)                 # as the pipeline groups them
    groups = [[items[i] for i in g] for g in clustering.cluster(items, itn.get("ongoing"))]
    matched = match_itn(groups, itn.get("items", []))
    active = len({i["outlet"] for i in items if i["published"] and i["published"] >= now - SHOWN})
    return [(g, assess(g, locate(g), matched.get(k), now, active=active)) for k, g in enumerate(groups)]


# Questions ------------------------------------------------------------------

def _prepared(name: str) -> dict:
    """A snapshot's stories near the bar, best first (cached: one snapshot at a time)."""
    if name not in _cache:
        items, itn, taken = load_snapshot(name)
        stories = sorted(((g, a) for g, a in assessed(items, itn, taken) if a["outlets"] >= 2),
                         key=lambda s: -s[1]["score"])
        _cache.clear()
        _cache[name] = {"stories": stories[:RANK_POOL], "taken": taken}
    return _cache[name]


def _name(a: dict) -> str:
    return BY_KEY[a["source"]].name if a["source"] in BY_KEY else a["outlet"]


def _story(group: dict, assessment: dict) -> dict:
    outlets = sorted({_name(a) for a in group})
    newest = sorted(group, key=lambda a: a["published"] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return {"headline": assessment["headline"], "urls": [a["url"] for a in group], "outlets": outlets,
            "titles": [a["title"] for a in newest[:4]]}


def rank_question(name: str, rng: random.Random) -> dict | None:
    stories = _prepared(name)["stories"]
    asked = {frozenset((r.a["headline"], r.b["headline"])) for r in Label.query.filter_by(kind="rank")}
    pairs = [(s, t) for n, s in enumerate(stories) for t in stories[n + 1:]
             if s[1]["score"] <= RANK_RATIO * t[1]["score"]
             and frozenset((s[1]["headline"], t[1]["headline"])) not in asked]
    if not pairs:
        return None
    s, t = rng.choice(pairs)
    if rng.random() < 0.5:
        s, t = t, s        # which side the higher score is on says nothing
    return {"kind": "rank", "snapshot": name, "a": _story(*s), "b": _story(*t)}


def show_question(name: str, rng: random.Random) -> dict | None:
    """One story near the bar: would you want it on your page today?"""
    asked = {r.a["headline"] for r in Label.query.filter_by(kind="show", source="you")}
    stories = [s for s in _prepared(name)["stories"] if s[1]["headline"] not in asked]
    if not stories:
        return None
    return {"kind": "show", "snapshot": name, "story": _story(*rng.choice(stories))}


def next_question(now: datetime, rng: random.Random | None = None) -> dict | None:
    """A ranking or a "would you show it?", in the MIX proportions."""
    rng = rng or random.Random()
    name = current_snapshot(now)
    ask = {"rank": lambda: rank_question(name, rng), "show": lambda: show_question(name, rng)}
    first = "rank" if rng.random() < MIX["rank"] else "show"
    for kind in [first, *(k for k in ask if k != first)]:
        q = ask[kind]()
        if q:
            return q
    return None


def record(answer: dict) -> Label:
    """Store an answer to a question from next_question (as the page sends it back)."""
    kind, value = answer.get("kind"), answer.get("answer")
    if value not in {"rank": RANK_ANSWERS, "show": SHOW_ANSWERS}.get(kind, ()):
        raise ValueError("unknown question or answer")
    load_snapshot(answer.get("snapshot"))      # it must be one of ours
    if kind == "show":
        story = answer.get("story")
        if not (isinstance(story, dict) and story.get("headline") and isinstance(story.get("urls"), list)):
            raise ValueError("incomplete question")
        a, b = {k: story[k] for k in ("headline", "urls", "outlets") if k in story}, {}
    else:
        a, b = answer.get("a"), answer.get("b")
        if not all(isinstance(x, dict) and "headline" in x and "urls" in x for x in (a, b)):
            raise ValueError("incomplete question")
        a, b = ({k: x[k] for k in ("headline", "urls", "outlets") if k in x} for x in (a, b))
    label = Label(kind=kind, snapshot=answer["snapshot"], a=a, b=b, answer=value)
    db.session.add(label)
    db.session.commit()
    return label


def counts(now: datetime) -> dict:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    yours = Label.query.filter_by(source="you")
    return {"today": yours.filter(Label.at >= today).count(), "total": yours.count()}


# What the answers say -------------------------------------------------------------

def _home(urls: list[str], where: dict[str, int]) -> int | None:
    """The group holding most of a judged story's articles."""
    votes: dict[int, int] = {}
    for u in urls:
        if u in where:
            votes[where[u]] = votes.get(where[u], 0) + 1
    return max(votes, key=votes.get) if votes else None


def _by_snapshot(labels: list[Label]):
    """(snapshot's articles, ITN, time taken, its labels) for each snapshot still on disk."""
    by: dict[str, list[Label]] = {}
    for label in labels:
        by.setdefault(label.snapshot, []).append(label)
    for name, group_labels in by.items():
        try:
            yield (*load_snapshot(name), group_labels)
        except (OSError, ValueError):
            continue


def _by_snapshot(labels: list[Label]):
    """(snapshot's articles, ITN, time taken, its labels) for each snapshot still on disk."""
    by: dict[str, list[Label]] = {}
    for label in labels:
        by.setdefault(label.snapshot, []).append(label)
    for name, group_labels in by.items():
        try:
            yield (*load_snapshot(name), group_labels)
        except (OSError, ValueError):
            continue


def evaluate(labels: list[Label]) -> dict:
    """Ranking: how often the higher score is the one you picked ("equal" left
    out). The bar: how often a story's tier agrees with whether you'd show it
    ("borderline" left out)."""
    rank = {"agree": 0, "disagree": 0}
    bar = {"agree": 0, "disagree": 0}
    for items, itn, taken, group_labels in _by_snapshot(labels):
        stories = assessed(items, itn, taken)
        where = {a["url"]: n for n, (g, _) in enumerate(stories) for a in g}
        for label in group_labels:
            if label.kind == "show" and label.answer != "borderline":
                home = _home(label.a["urls"], where)
                if home is not None:
                    shown = stories[home][1]["tier"] != "background"
                    bar["agree" if shown == (label.answer == "yes") else "disagree"] += 1
            elif label.kind == "rank" and label.answer != "equal":
                ga, gb = _home(label.a["urls"], where), _home(label.b["urls"], where)
                if ga is None or gb is None:
                    continue
                sa, sb = stories[ga][1]["score"], stories[gb][1]["score"]
                if sa == sb:
                    rank["disagree"] += 1      # a tie doesn't put the right one first
                else:
                    rank["agree" if (sa > sb) == (label.answer == "a") else "disagree"] += 1
    judged, barred = sum(rank.values()), sum(bar.values())
    return {"rank_pairs": judged, "rank_agreement": rank["agree"] / judged if judged else None,
            "bar_stories": barred, "bar_agreement": bar["agree"] / barred if barred else None}


def rank_examples(labels: list[Label]) -> list[tuple[list[float], list[float], bool]]:
    """Your rankings as examples for learn.fit_rank: both stories' score parts
    and which you picked. Stories are grouped as the app groups them."""
    import learn
    from score import rank_features
    out = []
    with learn.using({}):
        for items, itn, taken, group_labels in _by_snapshot([lb for lb in labels if lb.kind == "rank"]):
            stories = assessed(items, itn, taken)
            where = {a["url"]: n for n, (g, _) in enumerate(stories) for a in g}
            for label in group_labels:
                if label.answer == "equal":
                    continue
                ga, gb = _home(label.a["urls"], where), _home(label.b["urls"], where)
                if ga is not None and gb is not None and ga != gb:
                    out.append((rank_features(stories[ga][1]["parts"]), rank_features(stories[gb][1]["parts"]),
                                label.answer == "a"))
    return out


def place_examples(windows) -> list:
    """The ground truth's events with a place (groundtruth.py) as examples for
    learn.fit_place: each event's candidate countries and the one it's in."""
    import learn
    out = []
    for w in windows:
        members: dict[str, list[dict]] = {}
        for a in w.items:
            members.setdefault(w.truth[a["url"]], []).append(a)
        for event, arts in members.items():
            truth = w.places.get(event)
            if truth:
                isos, x = learn.place_candidates(arts)
                if truth in isos:
                    out.append((x, isos.index(truth)))
    return out


def split_holdout(labels: list[Label], days: int = HOLDOUT_DAYS) -> tuple[list[Label], list[Label]]:
    """(older, held out): the last `days` days of labelled snapshots are held out."""
    if not labels:
        return [], []
    newest = max(datetime.strptime(lb.snapshot, "%Y-%m-%dT%H%M") for lb in labels)
    cut = newest - timedelta(days=days)
    held = [lb for lb in labels if datetime.strptime(lb.snapshot, "%Y-%m-%dT%H%M") > cut]
    return [lb for lb in labels if lb not in held], held


def prune_snapshots(days: int = 30):
    """Drop snapshots over `days` old that nothing was judged on."""
    judged = {name for (name,) in db.session.query(Label.snapshot).distinct()}
    cutoff = utcnow() - timedelta(days=days)
    for path in snapshots_dir().glob("*.json.gz"):
        name = path.name.removesuffix(".json.gz")
        if name not in judged and _NAME.match(name) and datetime.strptime(name, "%Y-%m-%dT%H%M") < cutoff:
            path.unlink()
