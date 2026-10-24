"""Small models learned from your labels on /label, in place of hand-set thresholds.

Three decisions the app makes, each a model small enough to read:

  pairs   are two articles one story? A logistic regression over a handful of
          signals: word likeness (all words, the headline's, only the words
          that say what happened), meaning likeness (embed.py), same or other
          country, hours apart, same outlet, a wire. Grouping is then average
          linkage on its probabilities: two groups join when their articles,
          on average, are more likely one story than not. It replaces the
          hand-tuned THRESHOLD, cores, EVENT and PLACE_SHARE in cluster.py.
  rank    which story matters more? The score keeps its shape, (breadth + lead
          + Wikipedia + home region) × freshness, and the weights of the parts
          are fitted to your "which matters more" answers (Bradley–Terry), so
          "a Wikipedia match is worth two outlets" becomes what you showed.
  place   where is it happening? A choice among the countries a story names
          (conditional logit), from how each is named: in headlines or only
          summaries, as a place or only as an actor ("Washington says") or a
          leader ("Trump"), first or later; or nowhere in particular.

`flask train` fits them on the older labels and compares them with the hand
rules on the held-out ones; `flask train --save` fits them on everything and
writes instance/models/<name>.json, which the app then uses. Delete the file
(or set LEARNED=0) and the hand rules come back. With a few hundred labels
these are the right size: a model with a handful of weights can't memorise
your answers, and you can read what it learned.
"""
import json
import math
import os
import pathlib
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime

import numpy as np

import embed
from cluster import cosine, document, names, tfidf, tokens, where
from geo import mentions

NAMES = ("pairs", "rank", "place")


# Saved models ---------------------------------------------------------------

_override: dict | None = None        # set by using(): models not (yet) saved, or none at all
_cache: dict[str, tuple[float, dict]] = {}


def models_dir() -> pathlib.Path | None:
    from flask import current_app, has_app_context
    if not has_app_context():
        return None
    default = pathlib.Path(current_app.instance_path) / "models"
    return pathlib.Path(current_app.config.get("MODELS_DIR") or default)


def load(name: str) -> dict | None:
    """The saved model of that name, or None (the hand rules apply)."""
    if _override is not None:
        return _override.get(name)
    if os.environ.get("LEARNED", "1") == "0":
        return None
    folder = models_dir()
    if folder is None:
        return None
    path = folder / f"{name}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if name not in _cache or _cache[name][0] != mtime:
        _cache[name] = (mtime, json.loads(path.read_text()))
    model = _cache[name][1]
    if model.get("embed") and not embed.available():
        return None                   # trained with meaning likeness, which isn't here
    return model


def save(name: str, model: dict) -> pathlib.Path:
    folder = models_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(model, indent=1))
    return path


@contextmanager
def using(models: dict | None):
    """Use these models (name → model) instead of the saved ones; {} for the hand rules."""
    global _override
    before, _override = _override, models
    try:
        yield
    finally:
        _override = before


# Fitting --------------------------------------------------------------------

def fit_logistic(x: np.ndarray, y: np.ndarray, l2: float = 1.0, steps: int = 2000) -> dict:
    """A logistic regression on standardised features, with an L2 penalty so a
    few labels can't push any weight far. Plain gradient descent: the data is
    hundreds of rows."""
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale == 0] = 1.0
    z = (x - mean) / scale
    w, b, n = np.zeros(x.shape[1]), 0.0, len(y)
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(z @ w + b)))
        w -= 0.5 * ((z.T @ (p - y)) / n + l2 * w / n)
        b -= 0.5 * float((p - y).mean())
    return {"mean": mean.tolist(), "scale": scale.tolist(), "weights": w.tolist(), "bias": b}


def predict_logistic(model: dict, x: np.ndarray) -> np.ndarray:
    z = (x - np.array(model["mean"])) / np.array(model["scale"])
    return 1 / (1 + np.exp(-(z @ np.array(model["weights"]) + model["bias"])))


# Pairs: are two articles one story? ------------------------------------------

PAIR_FEATURES = ["likeness", "headline_likeness", "event_likeness", "meaning_likeness",
                 "same_country", "other_country", "hours_apart", "same_outlet", "wire"]
WIRES = {"ap", "reuters"}
CANDIDATE = 0.05       # word likeness below which a pair isn't scored (they share next to nothing)
MEANING_CANDIDATE = 0.5
LINK = 0.2             # probability that puts two articles in one bundle to be split
MERGE = 0.5            # average probability at which two groups are one story


def _unit(v: dict) -> dict:
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norm for t, x in v.items()}


def _main(found: Counter) -> str | None:
    if not found:
        return None
    iso, n = found.most_common(1)[0]
    return iso if n >= 0.5 * sum(found.values()) else None


class Pairs:
    """Everything the pair features need about a list of articles, worked out once."""

    def __init__(self, articles: list[dict], meaning: bool | None = None):
        self.articles = articles
        self.vectors = tfidf([document(a) for a in articles])
        self.headlines = tfidf([tokens(a["title"]) for a in articles])
        named = names(articles)
        self.events = [_unit({t: x for t, x in v.items() if t not in named and not t.isdigit()})
                       for v in self.vectors]
        self.country = [_main(where(a)) for a in articles]
        self.outlet = [a.get("outlet") or a["source"] for a in articles]
        self.wire = [a["source"] in WIRES for a in articles]
        self.time = [(a.get("published") or datetime.now(UTC)).timestamp() for a in articles]
        use = embed.available() if meaning is None else meaning
        self.meaning = embed.encode([embed.text(a) for a in articles]) if use else None

    def features(self, i: int, j: int) -> list[float]:
        ci, cj = self.country[i], self.country[j]
        hours = abs(self.time[i] - self.time[j]) / 3600
        return [
            cosine(self.vectors[i], self.vectors[j]),
            cosine(self.headlines[i], self.headlines[j]),
            cosine(self.events[i], self.events[j]),
            float(self.meaning[i] @ self.meaning[j]) if self.meaning is not None else 0.0,
            float(bool(ci and ci == cj)),
            float(bool(ci and cj and ci != cj)),
            math.log1p(hours),
            float(self.outlet[i] == self.outlet[j]),
            float(self.wire[i] or self.wire[j]),
        ]

    def candidates(self) -> set[tuple[int, int]]:
        """Pairs worth scoring: they share words, or mean much the same."""
        index: dict[str, list[int]] = {}
        for i, v in enumerate(self.vectors):
            for t in v:
                index.setdefault(t, []).append(i)
        out = set()
        for i, v in enumerate(self.vectors):
            for j in {j for t in v for j in index[t] if j > i}:
                if cosine(v, self.vectors[j]) >= CANDIDATE:
                    out.add((i, j))
        if self.meaning is not None and len(self.articles) > 1:
            sims = self.meaning @ self.meaning.T
            for i, j in zip(*np.nonzero(np.triu(sims, 1) >= MEANING_CANDIDATE), strict=True):
                out.add((int(i), int(j)))
        return out

    def probabilities(self, model: dict, pairs) -> dict[tuple[int, int], float]:
        pairs = list(pairs)
        if not pairs:
            return {}
        x = np.array([self.features(i, j) for i, j in pairs])
        return dict(zip(pairs, predict_logistic(model, x[:, _columns(model)]).tolist(), strict=True))


def _columns(model: dict) -> list[int]:
    return [PAIR_FEATURES.index(f) for f in model["features"]]


def group(articles: list[dict], model: dict) -> list[list[int]]:
    """Indexes of `articles` grouped by the pair model, as cluster.cluster returns
    them: biggest first, each group's most typical article first."""
    ctx = Pairs(articles, meaning=bool(model.get("embed")))
    p = ctx.probabilities(model, ctx.candidates())
    near: dict[int, dict[int, float]] = {i: {} for i in range(len(articles))}
    parent = list(range(len(articles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for (i, j), q in p.items():
        near[i][j] = near[j][i] = q
        if q >= LINK:
            parent[find(i)] = find(j)
    bundles: dict[int, list[int]] = {}
    for i in range(len(articles)):
        bundles.setdefault(find(i), []).append(i)
    out = []
    for members in bundles.values():
        for g in _average_linkage(members, near):
            fit = {i: sum(near[i].get(j, 0.0) for j in g) for i in g}
            out.append(sorted(g, key=lambda i: -fit[i]))
    return sorted(out, key=len, reverse=True)


def _average_linkage(members: list[int], near: dict[int, dict[int, float]]) -> list[list[int]]:
    """Join the two groups whose articles are most alike on average, while that
    average is at least MERGE; pairs never scored count as 0."""
    parts = {i: [i] for i in members}
    link = {i: {j: q for j, q in near[i].items() if j in parts} for i in members}
    while True:
        best, pair = MERGE, None
        for a, row in link.items():
            for c, total in row.items():
                if a < c and total / (len(parts[a]) * len(parts[c])) >= best:
                    best, pair = total / (len(parts[a]) * len(parts[c])), (a, c)
        if pair is None:
            return list(parts.values())
        a, b = pair
        parts[a] += parts.pop(b)
        for c, total in link.pop(b).items():
            if c == a:
                continue
            del link[c][b]
            link[a][c] = link[c][a] = link[a].get(c, 0.0) + total
        link[a].pop(b, None)


def fit_pairs(examples: list[tuple[list[float], bool]], meaning: bool) -> dict | None:
    if len({y for _, y in examples}) < 2:
        return None
    features = [f for f in PAIR_FEATURES if meaning or f != "meaning_likeness"]
    cols = [PAIR_FEATURES.index(f) for f in features]
    x = np.array([f for f, _ in examples])[:, cols]
    y = np.array([float(s) for _, s in examples])
    return {"features": features, "embed": meaning, **fit_logistic(x, y)}


# Rank: which story matters more? ------------------------------------------------

RANK_PARTS = ["breadth", "lead", "itn", "home"]      # in the order of score.rank_features


def fit_rank(examples: list[tuple[list[float], list[float], bool]], steps: int = 3000) -> dict | None:
    """Bradley–Terry on the parts: the chance you pick A is σ(w·(a − b)). The
    weights stay positive (more outlets never makes a story matter less) and
    are scaled so breadth's is 1, like the hand-set score."""
    if len(examples) < 5:
        return None
    d = np.array([np.subtract(a, b) for a, b, _ in examples] + [np.subtract(b, a) for a, b, _ in examples])
    y = np.array([float(first) for *_, first in examples] + [float(not first) for *_, first in examples])
    w = np.ones(d.shape[1])
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(d @ w)))
        w -= 0.1 * ((d.T @ (p - y)) / len(y) + 0.01 * w)
        w = np.maximum(w, 0.0)
    if w[0] <= 0:
        return None
    return {"weights": dict(zip(RANK_PARTS, (w / w[0]).round(3).tolist(), strict=True))}


# Place: where is it happening? ------------------------------------------------------

PLACE_FEATURES = ["headlines", "headlines_as_place", "summaries", "share", "as_actor", "as_leader",
                  "first", "nowhere"]
NOWHERE = "none"


def place_candidates(articles: list[dict]) -> tuple[list[str], np.ndarray]:
    """The countries a story names (at most eight), then "nowhere in particular",
    and each one's features, in that order. The most typical article comes first."""
    n = max(len(articles), 1)
    stats: dict[str, Counter] = {}
    for a in articles:
        for field in ("title", "summary"):
            seen = set()
            for iso, kind in mentions(a.get(field) or ""):
                s = stats.setdefault(iso, Counter())
                s["mentions"] += 1
                weight = 0.4 if kind == "actor" else 0.3 if kind == "ambiguous" else 1.0
                s["weighted"] += (3 if field == "title" else 1) * weight
                s[kind] += 1
                if iso not in seen:
                    seen.add(iso)
                    s[field] += 1
                    if field == "title" and kind not in ("actor", "leader", "ambiguous"):
                        s["title_place"] += 1
    first = mentions(articles[0]["title"])[:1] if articles else []
    total = sum(s["weighted"] for s in stats.values()) or 1.0
    isos = sorted(stats, key=lambda i: -stats[i]["weighted"])[:8]
    rows = [[stats[i]["title"] / n, stats[i]["title_place"] / n, stats[i]["summary"] / n, stats[i]["weighted"] / total,
             stats[i]["actor"] / stats[i]["mentions"], stats[i]["leader"] / stats[i]["mentions"],
             float(bool(first) and first[0][0] == i), 0.0] for i in isos]
    return isos + [NOWHERE], np.array(rows + [[0.0] * (len(PLACE_FEATURES) - 1) + [1.0]])


def choose_place(model: dict, articles: list[dict]) -> str | None:
    isos, x = place_candidates(articles)
    best = isos[int(np.argmax(x @ np.array(model["weights"])))]
    return None if best == NOWHERE else best


def fit_place(examples: list[tuple[np.ndarray, int]], steps: int = 3000) -> dict | None:
    """Conditional logit: softmax over each story's candidates, with an L2 penalty."""
    if len(examples) < 5:
        return None
    w = np.zeros(len(PLACE_FEATURES))
    w[PLACE_FEATURES.index("share")] = 1.0          # start from the hand rule: most mentioned wins
    for _ in range(steps):
        grad = 0.05 * w
        for x, t in examples:
            s = x @ w
            p = np.exp(s - s.max())
            p /= p.sum()
            grad += x.T @ p - x[t]
        w -= 0.5 * grad / len(examples)
    return {"features": PLACE_FEATURES, "weights": w.round(4).tolist()}
