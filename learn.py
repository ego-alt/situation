"""Small models learned from your labels on /label, in place of hand-set thresholds.

The models, each small enough to read, saved as instance/models/<name>.json:

  pairs       stage 1 of grouping, articles into events (events.py)
  situations  stage 2, events into situations (situations.py); both are
              grouping.Layer, the same machine over different units
  rank        which story matters more? The score keeps its shape, (breadth +
              lead + Wikipedia + home region) x freshness, and the weights of
              the parts are fitted to your "which matters more" answers
              (Bradley-Terry), so "a Wikipedia match is worth two outlets"
              becomes what you showed.
  place       where is it happening? A choice among the countries a story
              names (conditional logit), from how each is named: in headlines
              or only summaries, as a place or only as an actor ("Washington
              says") or a leader ("Trump"), first or later; or nowhere.

Here: saving and loading them, the logistic regression they share, and the
rank and place models. `flask train` fits them holding out the newest
snapshots and compares them with the hand rules there; `flask train --save`
fits them on everything and writes the files the app then uses. Delete a
file (or set LEARNED=0) and the hand rules come back.
"""
import json
import os
import pathlib
from collections import Counter
from contextlib import contextmanager

import numpy as np

import embed
from geo import mentions

NAMES = ("pairs", "situations", "rank", "place")


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
