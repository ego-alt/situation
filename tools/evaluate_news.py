"""How well stage 1 tells news from not (news.py), against the ground truth
(evaluation/groundtruth.py), and training its model. Run on the Mac.

    uv run python -I tools/evaluate_news.py           # measure
    uv run python -I tools/evaluate_news.py --save    # and save the model the app uses

The class that matters is NOT news, what gets dropped:
  precision  of what's dropped, the share that truly isn't news (real news
             wrongly dropped costs most)
  recall     of what isn't news, the share dropped
Both with 95% bootstrap intervals (articles resampled). Methods: each rule
alone, the rules together, and the learned model (fit), at its best F1 and at
90% precision, where the app runs it. Only what reaches stage 1 is scored:
Google's topic pages are dropped at fetch (fetch.topic_page). The model is
scored by 5-fold cross-validation, so no article is judged by a model that saw
it, and once more trained on one window and tested on the other. Then a
breakdown by outlet. --save trains on every label and writes
instance/models/news.json, with the threshold that gave 90% precision here.
The Pi's instance/ belongs to the container's user, so copy it in through the
container (the app picks it up on its next fetch):

    ssh pi 'docker exec -i situation sh -c "mkdir -p /app/instance/models && cat > /app/instance/models/news.json"' \
        < instance/models/news.json
"""
import argparse
import json
import pathlib
import random
import sys
from collections import Counter
from datetime import date

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import check_news_labels  # noqa: E402
import label_news  # noqa: E402

import embed  # noqa: E402
import news  # noqa: E402
from evaluation import groundtruth  # noqa: E402
from fetch import topic_page  # noqa: E402
from sources import SOURCES  # noqa: E402

TARGET = 0.95
SHIP_PRECISION = 0.90     # the app drops what the model is this sure of


def scores(dropped: list[bool], truth: list[bool], draws: int = 1000, seed: int = 0) -> dict:
    """Precision and recall of dropping (truth: True = not news), with intervals."""
    def pr(idx):
        tp = sum(1 for i in idx if dropped[i] and truth[i])
        fp = sum(1 for i in idx if dropped[i] and not truth[i])
        fn = sum(1 for i in idx if not dropped[i] and truth[i])
        return (tp / (tp + fp) if tp + fp else 0.0), (tp / (tp + fn) if tp + fn else 0.0)
    n = len(truth)
    p, r = pr(range(n))
    rng = random.Random(seed)
    samples = sorted(pr([rng.randrange(n) for _ in range(n)]) for _ in range(draws))
    ps = sorted(s[0] for s in samples)
    rs = sorted(s[1] for s in samples)
    lo, hi = int(0.025 * draws), int(0.975 * draws) - 1
    return {"precision": p, "recall": r, "p_ci": (ps[lo], ps[hi]), "r_ci": (rs[lo], rs[hi]), "dropped": sum(dropped)}


# The learned model -------------------------------------------------------------

MEANING = 64          # the embedding's main directions kept, of 256
MIN_ARTICLES = 25     # a token counts once this many training articles carry it


def fit(articles: list[dict], not_news: list[bool]) -> dict:
    """The model news.chances applies, trained on these articles: the evidence,
    the embedding cut to its MEANING main directions (PCA), and tokens seen in
    MIN_ARTICLES or more articles (about 70), so about 150 numbers an article.
    Cutting from 548 (all 256 directions, tokens seen 3 times) cost 0.6 points
    of F1, within noise, and none of the recall at 90% precision."""
    from sklearn.decomposition import PCA
    from sklearn.linear_model import LogisticRegression
    seen = Counter(t for a in articles for t in set(news.tokens(a)))
    pca = PCA(MEANING, random_state=0).fit(embed.encode([news.meaning_text(a) for a in articles]))
    model = {"evidence": news.evidence_names(), "tokens": sorted(t for t, n in seen.items() if n >= MIN_ARTICLES),
             "mean": pca.mean_.tolist(), "components": pca.components_.tolist()}
    x = news.matrix(articles, model)
    lr = LogisticRegression(C=1.0, max_iter=5000, class_weight="balanced").fit(x, not_news)
    return model | {"weights": lr.coef_[0].tolist(), "bias": float(lr.intercept_[0]), "width": x.shape[1]}


def learned(articles: list[dict], truth: list[bool], splits, extra: list[dict] = (), trusted: list[bool] | None = None):
    """Each article's chance of not being news, from a model that never saw it:
    for each (train, test) split of the articles, a model trained on the train
    part (only its trusted labels, when given) plus the extra (training-only)
    articles scores the test part. Returns the chances and the feature count."""
    p, width = np.zeros(len(articles)), 0
    for train, test in splits:
        train = [i for i in train if trusted is None or trusted[i]]
        labels = [truth[i] for i in train] + [not a["news"] for a in extra]
        model = fit([articles[i] for i in train] + list(extra), labels)
        p[test], width = news.chances([articles[i] for i in test], model), model["width"]
    return p, width


def folds(truth: list[bool], k: int = 5):
    from sklearn.model_selection import StratifiedKFold
    return list(StratifiedKFold(k, shuffle=True, random_state=0).split(np.zeros(len(truth)), truth))


def by_window(names: list[str]):
    """Train on one window, test on another: closer to production, where the
    model meets a week it hasn't seen, and no story spans train and test."""
    return [(np.array([i for i, x in enumerate(names) if x != w]), np.array([i for i, x in enumerate(names) if x == w]))
            for w in sorted(set(names))]


def f1(s: dict) -> float:
    return 2 * s["precision"] * s["recall"] / ((s["precision"] + s["recall"]) or 1)


def operating(p: np.ndarray, truth: list[bool]) -> dict:
    """The threshold with the best F1, and for 90% and 95% precision the
    threshold with the most recall that keeps it, and that recall."""
    points = []
    for t in np.arange(0.02, 0.995, 0.005):
        s = scores(list(p >= t), truth, draws=1)
        points.append((float(t), s["precision"], s["recall"], f1(s)))
    at = {target: max([pt for pt in points if pt[1] >= target], key=lambda pt: pt[2], default=(1.0, 0, 0.0, 0))
          for target in (SHIP_PRECISION, TARGET)}
    return {"threshold": max(points, key=lambda pt: pt[3])[0],
            "recall_at": {k: pt[2] for k, pt in at.items()}, "threshold_at": {k: pt[0] for k, pt in at.items()}}


def table(methods: dict[str, list[bool]], truth: list[bool]):
    print(f"{'method':38} {'drops':>6}   {'precision':>22}   {'recall':>22}     F1")
    for name, dropped in methods.items():
        s = scores(dropped, truth)
        ok = "✓" if s["precision"] >= TARGET and s["recall"] >= TARGET else " "
        print(f"{name:38} {s['dropped']:6}   {s['precision']:5.1%} ({s['p_ci'][0]:.1%}–{s['p_ci'][1]:.1%})   "
              f"{s['recall']:5.1%} ({s['r_ci'][0]:.1%}–{s['r_ci'][1]:.1%})  "
              f"{f1(s):5.1%} {ok}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--save", action="store_true", help="train on every label and save instance/models/news.json")
    args = parser.parse_args()
    if not embed.available():
        sys.exit(f"no embedding model in {embed.MODEL_DIR}: fetch it as the Dockerfile does")
    google = {source.key for source in SOURCES if source.via_google}
    windows = groundtruth.windows()
    kept = [(w, a) for w in windows for a in w.articles
            if a["url"] in w.news and not (a["source"] in google and topic_page(a["title"]))]
    articles, truth = [a for _, a in kept], [not w.news[a["url"]] for w, a in kept]
    print(f"{len(articles)} articles from {len(windows)} windows, {sum(truth)} not news "
          f"({sum(truth) / len(truth):.0%})\n")
    methods = {
        "headline": [news.headline_says_not_news(a) for a in articles],
        "url": [news.url_says_not_news(a) for a in articles],
        "rules: headline or url (is_news)": [not news.is_news(a) for a in articles],
    }
    # A second labeller's labels (tools/check_news_labels.py), if any: train only on
    # labels both agree on, and score on those as well as on all.
    check = check_news_labels.load()
    trusted = [check.get(a["url"], not t) is not t for a, t in zip(articles, truth, strict=True)]
    extra = [a for a in label_news.load()["articles"].values() if check.get(a["url"], a["news"]) == a["news"]]
    names = [w.name for w, _ in kept]
    variants = {"learned": learned(articles, truth, folds(truth), extra, trusted),
                "learned, by window": learned(articles, truth, by_window(names), extra, trusted)}
    notes = [f"learned: {len(extra)} training-only articles besides the folds; by window: trained on one window, "
             "tested on the other"]
    if check:
        disputed = trusted.count(False)
        notes.append(f"second labeller: {sum(a['url'] in check for a in articles)} checked, {disputed} disputed "
                     f"({disputed / len(trusted):.1%}), left out of training")
    for name, (p, width) in variants.items():
        op = operating(p, truth)
        methods[name] = list(p >= op["threshold"])
        methods[f"{name}, at 90% precision"] = list(p >= op["threshold_at"][SHIP_PRECISION])
        notes.append(f"  {name:38} {width:5} features, threshold {op['threshold']:.2f} (best F1), "
                     f"{op['threshold_at'][SHIP_PRECISION]:.2f} (90% precision); recall at 95% precision "
                     f"{op['recall_at'][TARGET]:.1%}")
    table(methods, truth)
    if check:
        keep = [i for i, t in enumerate(trusted) if t]
        print(f"\nwhere both labellers agree ({len(keep)} articles):")
        table({name: [dropped[i] for i in keep] for name, dropped in methods.items()}, [truth[i] for i in keep])
    print("\n" + "\n".join(notes))
    dropped = methods["learned"]
    print("\nby outlet, learned:")
    for outlet, _ in Counter(a["source"] for a in articles).most_common():
        idx = [i for i, a in enumerate(articles) if a["source"] == outlet]
        s = scores([dropped[i] for i in idx], [truth[i] for i in idx], draws=1)
        nn = sum(truth[i] for i in idx)
        print(f"  {outlet:12} {len(idx):5} articles, {nn:4} not news   "
              f"precision {s['precision']:5.1%}   recall {s['recall']:5.1%}")
    if args.save:
        p, _ = variants["learned"]
        keep = [i for i, t in enumerate(trusted) if t]
        model = fit([articles[i] for i in keep] + extra, [truth[i] for i in keep] + [not a["news"] for a in extra])
        drops = methods["learned, at 90% precision"]
        s = scores(drops, truth, draws=1)
        model |= {"threshold": operating(p, truth)["threshold_at"][SHIP_PRECISION], "trained": date.today().isoformat(),
                  "articles": len(keep) + len(extra), "cross_validated": {"precision": round(s["precision"], 3),
                                                                          "recall": round(s["recall"], 3)}}
        path = ROOT / "instance" / "models" / "news.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(model))
        print(f"\nsaved {path.relative_to(ROOT)}: {model['width']} features, threshold {model['threshold']:.3f}, "
              f"trained on {model['articles']} articles")


if __name__ == "__main__":
    main()
