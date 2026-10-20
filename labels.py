"""Your judgements, to measure the grouping and ranking against.

Two kinds of question, asked on /label:
  same   two articles: would you want them under one headline? (Macron's
         response to the riots and the riots: yes, the page shows a running
         situation as one story with its latest development.) Half are pairs the grouping put in
         one story (the least alike of them), half pairs it kept apart that
         came close (likeness LOW..HIGH): the cases it most often gets wrong.
  rank   two stories: which matters more? Pairs are close calls among the
         stories near the bar (scores within a factor of RANK_RATIO).

Each question is asked about a saved snapshot of the articles (instance/
snapshots/), so `flask evaluate` can re-run any version of the code, or any
variant of its thresholds, on exactly what you judged:

    flask evaluate                          # the code as it is
    flask evaluate --set cluster.EVENT=0.25 --set score.COPY=0.9

It reports the most recent HOLDOUT_DAYS days of labels apart from the rest:
tune on the older ones, and believe a change only if the held-out numbers
agree. Questions are drawn from the hard cases, so the numbers are low next to
a random sample's; they are for comparing versions, not for quoting.
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

SAME_ANSWERS = {"same", "different", "unsure"}
RANK_ANSWERS = {"a", "b", "equal"}
LOW, HIGH = 0.18, 0.45        # likeness of the kept-apart "same story?" pairs
RANK_RATIO = 2.0
RANK_POOL = 40                # stories near the bar that rank questions draw from
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
    from score import assess, match_itn
    groups = [[items[i] for i in g] for g in clustering.cluster(items)]
    matched = match_itn(groups, itn.get("items", []))
    active = len({i["outlet"] for i in items if i["published"] and i["published"] >= now - SHOWN})
    return [(g, assess(g, locate(g), matched.get(k), now, active=active)) for k, g in enumerate(groups)]


# Questions ------------------------------------------------------------------

def _prepared(name: str) -> dict:
    """A snapshot's articles, their vectors and its stories near the bar (cached)."""
    if name not in _cache:
        items, itn, taken = load_snapshot(name)
        vectors = clustering.tfidf([clustering.document(a) for a in items])
        everything = assessed(items, itn, taken)
        index = {id(a): n for n, a in enumerate(items)}
        group_of = {index[id(a)]: k for k, (g, _) in enumerate(everything) for a in g}
        stories = sorted(((g, a) for g, a in everything if a["outlets"] >= 2), key=lambda s: -s[1]["score"])
        _cache.clear()
        _cache[name] = {"items": items, "vectors": vectors, "group_of": group_of,
                        "stories": stories[:RANK_POOL], "taken": taken}
    return _cache[name]


def _name(a: dict) -> str:
    return BY_KEY[a["source"]].name if a["source"] in BY_KEY else a["outlet"]


def _article(a: dict) -> dict:
    return {"url": a["url"], "title": a["title"], "outlet": _name(a),
            "summary": " ".join((a.get("summary") or "").split()[:40]),
            "published": a["published"].isoformat() if a["published"] else None}


def _story(group: dict, assessment: dict) -> dict:
    outlets = sorted({_name(a) for a in group})
    newest = sorted(group, key=lambda a: a["published"] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return {"headline": assessment["headline"], "urls": [a["url"] for a in group], "outlets": outlets,
            "titles": [a["title"] for a in newest[:4]]}


def _asked(kind: str) -> set[frozenset]:
    rows = Label.query.filter_by(kind=kind).all()
    if kind == "same":
        return {frozenset((r.a["url"], r.b["url"])) for r in rows}
    return {frozenset((r.a["headline"], r.b["headline"])) for r in rows}


def same_question(name: str, rng: random.Random) -> dict | None:
    p = _prepared(name)
    items, vectors, group_of = p["items"], p["vectors"], p["group_of"]
    asked = _asked("same")
    together = rng.random() < 0.5
    from score import is_headline
    since = p["taken"] - timedelta(hours=24)
    # Start from news (an article in a story two outlets carry), not a column on its own.
    outlets: dict[int, set] = {}
    for k, a in enumerate(items):
        outlets.setdefault(group_of[k], set()).add(a["outlet"])
    fit = [is_headline(a["title"]) and len(clustering.tokens(a["title"])) >= 4 for a in items]
    recent = [i for i, a in enumerate(items)
              if fit[i] and a["published"] and a["published"] >= since and len(outlets[group_of[i]]) >= 2]
    for i in rng.sample(recent, min(len(recent), 80)):
        pairs = []
        for j, a in enumerate(items):
            if not fit[j] or a["outlet"] == items[i]["outlet"] or frozenset((items[i]["url"], a["url"])) in asked:
                continue
            if (group_of[i] == group_of[j]) != together:
                continue
            likeness = clustering.cosine(vectors[i], vectors[j])
            if together or LOW <= likeness <= HIGH:
                pairs.append((likeness, j))
        if pairs:
            j = min(pairs)[1] if together else rng.choice(pairs)[1]
            return {"kind": "same", "snapshot": name, "a": _article(items[i]), "b": _article(items[j])}
    return None


def rank_question(name: str, rng: random.Random) -> dict | None:
    stories = _prepared(name)["stories"]
    asked = _asked("rank")
    pairs = [(s, t) for n, s in enumerate(stories) for t in stories[n + 1:]
             if s[1]["score"] <= RANK_RATIO * t[1]["score"]
             and frozenset((s[1]["headline"], t[1]["headline"])) not in asked]
    if not pairs:
        return None
    s, t = rng.choice(pairs)
    if rng.random() < 0.5:
        s, t = t, s        # which side the higher score is on says nothing
    return {"kind": "rank", "snapshot": name, "a": _story(*s), "b": _story(*t)}


def next_question(now: datetime, rng: random.Random | None = None) -> dict | None:
    rng = rng or random.Random()
    name = current_snapshot(now)
    first, second = (same_question, rank_question) if rng.random() < 0.5 else (rank_question, same_question)
    return first(name, rng) or second(name, rng)


def record(answer: dict) -> Label:
    """Store an answer to a question from next_question (as the page sends it back)."""
    kind, value = answer.get("kind"), answer.get("answer")
    if value not in {"same": SAME_ANSWERS, "rank": RANK_ANSWERS}.get(kind, ()):
        raise ValueError("unknown question or answer")
    load_snapshot(answer.get("snapshot"))      # it must be one of ours
    need, keep = ((("url", "title", "outlet"), ("url", "title", "outlet", "published")) if kind == "same"
                  else (("headline", "urls"), ("headline", "urls", "outlets")))
    a, b = answer.get("a"), answer.get("b")
    if not all(isinstance(x, dict) and all(k in x for k in need) for x in (a, b)):
        raise ValueError("incomplete question")
    a, b = ({k: x[k] for k in keep if k in x} for x in (a, b))
    label = Label(kind=kind, snapshot=answer["snapshot"], a=a, b=b, answer=value)
    db.session.add(label)
    db.session.commit()
    return label


def counts(now: datetime) -> dict:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    return {"today": Label.query.filter(Label.at >= today).count(), "total": Label.query.count()}


# Evaluation -----------------------------------------------------------------

def _home(urls: list[str], groups: list[list[dict]], where: dict[str, int]) -> int | None:
    """The group holding most of a judged story's articles."""
    votes: dict[int, int] = {}
    for u in urls:
        if u in where:
            votes[where[u]] = votes.get(where[u], 0) + 1
    return max(votes, key=votes.get) if votes else None


def evaluate(labels: list[Label]) -> dict:
    """Grouping: precision and recall on "same story" (unsure left out).
    Ranking: how often the higher score is the one you picked ("equal" left out)."""
    same = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    rank = {"agree": 0, "disagree": 0}
    by_snapshot: dict[str, list[Label]] = {}
    for label in labels:
        by_snapshot.setdefault(label.snapshot, []).append(label)
    for name, group_labels in by_snapshot.items():
        try:
            items, itn, taken = load_snapshot(name)
        except (OSError, ValueError):
            continue
        stories = assessed(items, itn, taken)
        where = {a["url"]: n for n, (g, _) in enumerate(stories) for a in g}
        for label in group_labels:
            if label.kind == "same" and label.answer != "unsure":
                ga, gb = where.get(label.a["url"]), where.get(label.b["url"])
                together = ga is not None and ga == gb
                truth = label.answer == "same"
                same[("t" if together == truth else "f") + ("p" if together else "n")] += 1
            elif label.kind == "rank" and label.answer != "equal":
                ga = _home(label.a["urls"], [g for g, _ in stories], where)
                gb = _home(label.b["urls"], [g for g, _ in stories], where)
                if ga is None or gb is None:
                    continue
                sa, sb = stories[ga][1]["score"], stories[gb][1]["score"]
                if sa == sb:
                    rank["disagree"] += 1      # a tie doesn't put the right one first
                else:
                    rank["agree" if (sa > sb) == (label.answer == "a") else "disagree"] += 1
    tp, fp, fn = same["tp"], same["fp"], same["fn"]
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    judged = rank["agree"] + rank["disagree"]
    return {"same_pairs": sum(same.values()), "precision": precision, "recall": recall, "f1": f1,
            "rank_pairs": judged, "rank_agreement": rank["agree"] / judged if judged else None}


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
