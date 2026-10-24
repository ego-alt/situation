"""Your judgements, to measure the grouping, ranking and placing against, and
to train the small models in learn.py.

Questions asked on /label:
  story  one story as the app grouped it: tap the articles that don't belong,
         then the nearby ones it left out that do, and say where it's
         happening. One screen gives a dozen or more "same story?" answers
         (every pair it implies) and a place. Stories are chosen where the
         grouping is least sure: members that fit loosely, and near misses.
  rank   two stories: which matters more? Pairs are close calls among the
         stories near the bar (scores within a factor of RANK_RATIO).
  same   (no longer asked; earlier answers still count) two articles: would
         you want them under one headline?

Each question is asked about a saved snapshot of the articles (instance/
snapshots/), so `flask evaluate` can re-run any version of the code, or any
variant of its thresholds, on exactly what you judged, and `flask train` can
fit the models on it:

    flask evaluate                          # the code as it is
    flask evaluate --hand                   # the hand rules, ignoring trained models
    flask evaluate --set cluster.EVENT=0.25 --set score.COPY=0.9
    flask train                             # fit on older labels, compare on held-out ones
    flask train --save                      # fit on all of them, and use the models

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
STORY_ANSWERS = {"checked"}
STORY_SHOWN = 10              # articles of a story shown at once: the most typical, then the loosest fits
NEARBY = 5                    # near misses shown under it
CANDIDATES = 12               # stories looked at to find the least certain one
LOW, HIGH = 0.18, 0.45        # without a model: likeness to a story that makes a near miss
LOOSE = 0.35                  # without a model: a member less like its story than this fits loosely
UNSURE = (0.2, 0.8)           # with a model: the probabilities it isn't sure about
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
    """A snapshot's articles, their vectors, its stories and the pair model's
    view of them (cached: one snapshot at a time)."""
    if name not in _cache:
        import learn
        items, itn, taken = load_snapshot(name)
        everything = assessed(items, itn, taken)
        index = {id(a): n for n, a in enumerate(items)}
        groups = [[index[id(a)] for a in g] for g, _ in everything]
        model = learn.load("pairs")
        stories = sorted(((g, a) for g, a in everything if a["outlets"] >= 2), key=lambda s: -s[1]["score"])
        _cache.clear()
        _cache[name] = {"items": items, "vectors": clustering.tfidf([clustering.document(a) for a in items]),
                        "groups": groups, "assessments": [a for _, a in everything], "stories": stories[:RANK_POOL],
                        "taken": taken, "model": model,
                        "pairs": learn.Pairs(items, meaning=bool(model.get("embed"))) if model else None}
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


def _fits(p: dict, members: list[int]) -> tuple[dict[int, float], dict[int, float]]:
    """How well each member fits its story, and how like it the closest
    non-members are: the pair model's average probability when there is one,
    else likeness to the story's centroid."""
    vectors = p["vectors"]
    centroid = clustering._centroid(members, vectors)
    inside = set(members)
    near = sorted(((clustering.cosine(v, centroid), j) for j, v in enumerate(vectors) if j not in inside),
                  reverse=True)[:40]
    if not p["model"]:
        return {i: clustering.cosine(vectors[i], centroid) for i in members}, {j: c for c, j in near if c > 0.05}
    pairs = {(min(i, j), max(i, j)) for i in members for j in members if i != j}
    pairs |= {(min(i, j), max(i, j)) for i in members for _, j in near}
    prob = p["pairs"].probabilities(p["model"], pairs)
    q = lambda i, j: prob.get((min(i, j), max(i, j)), 0.0)  # noqa: E731
    fit = {i: sum(q(i, j) for j in members if j != i) / max(len(members) - 1, 1) for i in members}
    return fit, {j: sum(q(i, j) for i in members) / len(members) for _, j in near}


def _unsure(value: float, member: bool, model: bool) -> bool:
    if model:
        return UNSURE[0] <= value <= UNSURE[1]
    return value < LOOSE if member else LOW <= value <= HIGH


def _asked_story_urls(name: str) -> set[str]:
    rows = Label.query.filter_by(kind="story", snapshot=name).all()
    return {u for r in rows for u in r.a["urls"][:1]} | {u for r in rows for u in r.a["urls"] + r.b.get("in", [])}


def story_question(name: str, rng: random.Random) -> dict | None:
    """The least certain of a few recent stories, as grouped: its articles (the
    most typical, then the loosest fits), the near misses, and where it's placed."""
    from geo import _table, locate
    from learn import NOWHERE, place_candidates
    p = _prepared(name)
    items, model = p["items"], bool(p["model"])
    since = p["taken"] - timedelta(hours=24)
    asked = _asked_story_urls(name)
    fresh = [g for g in p["groups"]
             if len({items[i]["outlet"] for i in g}) >= 2 and items[g[0]]["url"] not in asked
             and any(items[i]["published"] and items[i]["published"] >= since for i in g)]
    best = None
    for g in rng.sample(fresh, min(len(fresh), CANDIDATES)):
        fit, near = _fits(p, g)
        doubt = (sum(_unsure(v, True, model) for v in fit.values())
                 + sum(_unsure(v, False, model) for v in near.values()))
        if best is None or doubt > best[0]:
            best = (doubt, g, fit, near)
    if best is None:
        return None
    _, g, fit, near = best
    shown = g if len(g) <= STORY_SHOWN else g[:3] + sorted(g[3:], key=fit.get)[:STORY_SHOWN - 3]
    close = sorted(near, key=near.get, reverse=True)
    nearby = [j for j in close if _unsure(near[j], False, model)][:NEARBY] or close[:2]
    articles = [items[i] for i in shown]
    guess = locate(articles)
    countries = _table()[0]
    options = [i for i in place_candidates(articles)[0] if i != NOWHERE][:5]
    if guess and guess["iso"] not in options:
        options.insert(0, guess["iso"])
    return {"kind": "story", "snapshot": name, "headline": p["assessments"][p["groups"].index(g)]["headline"],
            "members": [_article(a) for a in articles], "nearby": [_article(items[j]) for j in nearby],
            "place": {"guess": guess["iso"] if guess else None,
                      "options": [[i, countries[i]["name"]] for i in options if i in countries]}}


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
    """Mostly stories (each answers many questions at once), one in four a ranking."""
    rng = rng or random.Random()
    name = current_snapshot(now)
    first, second = (rank_question, story_question) if rng.random() < 0.25 else (story_question, rank_question)
    return first(name, rng) or second(name, rng)


def record(answer: dict) -> Label:
    """Store an answer to a question from next_question (as the page sends it back)."""
    kind, value = answer.get("kind"), answer.get("answer")
    if value not in {"same": SAME_ANSWERS, "rank": RANK_ANSWERS, "story": STORY_ANSWERS}.get(kind, ()):
        raise ValueError("unknown question or answer")
    load_snapshot(answer.get("snapshot"))      # it must be one of ours
    if kind == "story":
        a, b = _story_answer(answer)
    else:
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


def _story_answer(answer: dict) -> tuple[dict, dict]:
    """(what was shown, what you said) for a story question."""
    from geo import _table
    from learn import NOWHERE
    try:
        urls = [m["url"] for m in answer["members"]]
        nearby = [m["url"] for m in answer.get("nearby") or []]
        guess = (answer.get("place") or {}).get("guess")
    except (KeyError, TypeError) as e:
        raise ValueError("incomplete question") from e
    out, joined = answer.get("out") or [], answer.get("in") or []
    where = answer.get("where")
    if not (urls and set(out) <= set(urls) and set(joined) <= set(nearby)):
        raise ValueError("those articles weren't in the question")
    if where is not None and where != NOWHERE and where not in _table()[0]:
        raise ValueError("not a country")
    return ({"headline": answer.get("headline"), "urls": urls, "nearby": nearby, "guess": guess},
            {"out": out, "in": joined, "place": where})


def counts(now: datetime) -> dict:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    return {"today": Label.query.filter(Label.at >= today).count(), "total": Label.query.count()}


# What the answers say -------------------------------------------------------------

def story_urls(label: Label) -> list[str]:
    """The story as you'd have it: what was shown, minus what you took out,
    plus the near misses you put in (most typical first)."""
    out, joined = set(label.b.get("out", [])), set(label.b.get("in", []))
    return [u for u in label.a["urls"] if u not in out] + [u for u in label.a.get("nearby", []) if u in joined]


def judged_pairs(label: Label) -> list[tuple[str, str, bool]]:
    """Every "same story?" answer a label gives: (url, url, same)."""
    if label.kind == "same":
        return [] if label.answer == "unsure" else [(label.a["url"], label.b["url"], label.answer == "same")]
    if label.kind != "story":
        return []
    kept = story_urls(label)
    apart = set(label.b.get("out", [])) | {u for u in label.a.get("nearby", []) if u not in kept}
    return ([(x, y, True) for n, x in enumerate(kept) for y in kept[n + 1:]]
            + [(x, y, False) for x in sorted(apart) for y in kept])


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


def evaluate(labels: list[Label]) -> dict:
    """Grouping: precision and recall on "same story" (unsure left out).
    Ranking: how often the higher score is the one you picked ("equal" left out).
    Placing: how often the story you checked is put where you said."""
    from geo import locate
    from learn import NOWHERE
    same = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    rank = {"agree": 0, "disagree": 0}
    place = {"right": 0, "wrong": 0}
    for items, itn, taken, group_labels in _by_snapshot(labels):
        stories = assessed(items, itn, taken)
        where = {a["url"]: n for n, (g, _) in enumerate(stories) for a in g}
        by_url = {a["url"]: a for a in items}
        for label in group_labels:
            for u, v, truth in judged_pairs(label):
                together = where.get(u) is not None and where.get(u) == where.get(v)
                same[("t" if together == truth else "f") + ("p" if together else "n")] += 1
            if label.kind == "story" and label.b.get("place") is not None:
                found = locate([by_url[u] for u in story_urls(label) if u in by_url])
                place["right" if (found["iso"] if found else NOWHERE) == label.b["place"] else "wrong"] += 1
            elif label.kind == "rank" and label.answer != "equal":
                ga, gb = _home(label.a["urls"], where), _home(label.b["urls"], where)
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
    judged, placed = rank["agree"] + rank["disagree"], place["right"] + place["wrong"]
    return {"same_pairs": sum(same.values()), "precision": precision, "recall": recall, "f1": f1,
            "rank_pairs": judged, "rank_agreement": rank["agree"] / judged if judged else None,
            "placed": placed, "place_accuracy": place["right"] / placed if placed else None}


def training_data(labels: list[Label]) -> dict:
    """The labels as examples for learn.py: pair features with same-or-not,
    the score's parts for both sides of a ranking, and each checked story's
    place candidates with the one you chose. Stories are grouped by the hand
    rules here, so the examples don't depend on a model already trained."""
    import embed
    import learn
    from score import rank_features
    meaning = embed.available()
    data = {"pairs": [], "rank": [], "place": [], "meaning": meaning}
    with learn.using({}):
        for items, itn, taken, group_labels in _by_snapshot(labels):
            index = {a["url"]: n for n, a in enumerate(items)}
            kinds = {lb.kind for lb in group_labels}
            ctx = learn.Pairs(items, meaning=meaning) if kinds & {"same", "story"} else None
            stories = assessed(items, itn, taken) if "rank" in kinds else []
            where = {a["url"]: n for n, (g, _) in enumerate(stories) for a in g}
            for label in group_labels:
                for u, v, same in judged_pairs(label):
                    if u in index and v in index:
                        data["pairs"].append((ctx.features(index[u], index[v]), same))
                if label.kind == "story" and label.b.get("place") is not None:
                    isos, x = learn.place_candidates([items[index[u]] for u in story_urls(label) if u in index])
                    if label.b["place"] in isos:
                        data["place"].append((x, isos.index(label.b["place"])))
                if label.kind == "rank" and label.answer != "equal":
                    ga, gb = _home(label.a["urls"], where), _home(label.b["urls"], where)
                    if ga is not None and gb is not None and ga != gb:
                        data["rank"].append((rank_features(stories[ga][1]["parts"]),
                                             rank_features(stories[gb][1]["parts"]), label.answer == "a"))
    return data


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
