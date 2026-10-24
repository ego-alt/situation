"""Compare grouping methods on the event ground truth (groundtruth.py). Run on the Mac.

    uv run python -I tools/evaluate_grouping.py                      # every method in METHODS
    uv run python -I tools/evaluate_grouping.py --methods rules,meaning --dir path/to/groundtruth

Protocol: the newest week is the test; each method's dial (if it has one) is
tuned on the older weeks, on headline-size B-cubed F1. While there's only one
week, each window takes a turn as the test. Reported per method, against the
deployed hand rules: B-cubed (metrics.py) over all articles and over
headline-size events, and the difference with a 95% bootstrap interval. Then
the test windows' reviews (groundtruth.describe).

To try a new method, add it to METHODS: a function of (articles, dial) giving
groups of article indexes, and the dial values to tune over ([None] if none).
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import cluster  # noqa: E402
import groundtruth  # noqa: E402
import metrics  # noqa: E402

BASELINE = "rules"
METHODS = {
    "rules": (lambda items, dial: cluster.by_rules(items), [None]),        # what's deployed
    "meaning": (lambda items, cut: cluster.by_meaning(items, cut), [0.3, 0.35, 0.4, 0.45, 0.5, 0.55]),
}


def found(window: groundtruth.Window, groups: list[list[int]]) -> dict[str, str]:
    return {window.items[i]["url"]: f"{window.name}/{k}" for k, g in enumerate(groups) for i in g}


def folds(windows: list[groundtruth.Window]) -> list[tuple[list, list]]:
    """(tune on, test on): the newest week against the rest, or each window in turn."""
    weeks = sorted({w.week for w in windows})
    if len(weeks) > 1:
        return [([w for w in windows if w.week != weeks[-1]], [w for w in windows if w.week == weeks[-1]])]
    return [([v for v in windows if v is not w], [w]) for w in windows]


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dir", default=None, help="the ground truth folder (default: groundtruth.directory())")
    p.add_argument("--methods", default=",".join(METHODS))
    args = p.parse_args()
    chosen = [m for m in args.methods.split(",") if m in METHODS]
    if BASELINE not in chosen:
        chosen.insert(0, BASELINE)
    windows = groundtruth.windows(args.dir)
    if not windows:
        sys.exit(f"no ground truth in {groundtruth.directory(args.dir)}: run tools/label_week.py")
    cache: dict[tuple, dict[str, str]] = {}

    def grouping(method, window, dial):
        key = (method, window.week, window.name, dial)
        if key not in cache:
            cache[key] = found(window, METHODS[method][0](window.items, dial))
        return cache[key]

    def headline_f1(method, dial, ws):
        parts = {}
        for w in ws:
            big = metrics.headline_articles(w.truth, w.outlet)
            parts |= {u: pr for u, pr in metrics.bcubed(w.truth, grouping(method, w, dial)).items() if u in big}
        return metrics.summary(parts.values())["f1"] or 0.0

    truth, outlet, tested = {}, {}, []
    parts = {m: {} for m in chosen}
    dials = {m: [] for m in chosen}
    for tune, test in folds(windows):
        for m in chosen:
            dial = max(METHODS[m][1], key=lambda d, m=m: headline_f1(m, d, tune)) if len(METHODS[m][1]) > 1 else None
            dials[m].append(dial)
            for w in test:
                parts[m] |= metrics.bcubed(w.truth, grouping(m, w, dial))
        for w in test:
            truth |= w.truth
            outlet |= w.outlet
            tested.append(w)
    big = metrics.headline_articles(truth, outlet)
    print(f"tested on {len(tested)} windows ({len(truth)} articles, {len({truth[u] for u in big})} headline-size "
          f"events), {'the newest week' if len({w.week for w in windows}) > 1 else 'each window in turn'}\n")
    print(f"{'method':10} {'dial':>12}   {'all articles':>24}   {'headline-size':>24}")
    pct = lambda x: f"{x:4.0%}"  # noqa: E731
    for m in chosen:
        a, h = metrics.summary(parts[m].values()), metrics.summary(pr for u, pr in parts[m].items() if u in big)
        line = (f"{m:10} {','.join(str(d) for d in dials[m]) if dials[m][0] is not None else '—':>12}   "
                f"P {pct(a['precision'])} R {pct(a['recall'])} F1 {pct(a['f1'])}   "
                f"P {pct(h['precision'])} R {pct(h['recall'])} F1 {pct(h['f1'])}")
        if m != BASELINE:
            d_all = metrics.difference(parts[BASELINE], parts[m], truth)
            d_big = metrics.difference({u: v for u, v in parts[BASELINE].items() if u in big},
                                       {u: v for u, v in parts[m].items() if u in big}, truth)
            versus = (f"{name} {d:+.1%} ({lo:+.1%} to {hi:+.1%})"
                      for name, (d, lo, hi) in (("all", d_all), ("headline", d_big)))
            line += "\n" + " " * 26 + "vs rules: " + "   ".join(versus)
        print(line)
    print()
    for w in tested:
        print(groundtruth.describe(w))


if __name__ == "__main__":
    main()
