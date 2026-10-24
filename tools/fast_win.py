"""Off-the-shelf grouping (cluster.by_meaning) against what's deployed (the
hand rules, cluster.by_rules), on Claude's labels. Run on the Mac.

    uv run python -I tools/fast_win.py <snapshots dir> <claude labels .json.gz>

For each snapshot with enough labels in turn: by_meaning's cut is tuned on the
other snapshots (best B-cubed F1), then both are scored on the held-out one,
over all labelled articles and over headline-size stories (labels.
HEADLINE_OUTLETS or more outlets: those the page can show). The difference
gets a 95% bootstrap interval: the held-out stories resampled 2,000 times; if
the interval stays above zero, the win isn't luck.
"""
import gzip
import json
import pathlib
import random
import shutil
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CUTS = [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75]


def f1(parts):
    if not parts:
        return 0.0
    p = sum(x for x, _ in parts) / len(parts)
    r = sum(y for _, y in parts) / len(parts)
    return 2 * p * r / (p + r) if p + r else 0.0


def sizes(groups, items):
    """(stories, stories three or more outlets carry)."""
    return len(groups), sum(len({items[i]["outlet"] for i in g}) >= 3 for g in groups)


def main():
    snaps_dir, claude_file = sys.argv[1:3]
    scratch = pathlib.Path(tempfile.mkdtemp())
    shutil.copytree(snaps_dir, scratch / "snapshots")
    from app import create_app
    app = create_app({"TESTING": True, "FETCH_LOOP": False, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{scratch / 'x.db'}",
                      "SNAPSHOTS_DIR": str(scratch / "snapshots")})
    with app.app_context():
        import cluster
        from labels import bcubed_parts, headline_articles, import_labels, judged_pairs, load_snapshot, snapshot_truth
        from models import Label
        with gzip.open(claude_file, "rt", encoding="utf-8") as f:
            import_labels(json.load(f))
        by_snap: dict[str, list] = {}
        for lb in Label.query.all():
            by_snap.setdefault(lb.snapshot, []).append(lb)
        data = {}
        for name, lbs in sorted(by_snap.items()):
            items, _, _ = load_snapshot(name)
            truth = snapshot_truth(lbs)
            if sum(len(judged_pairs(lb)) for lb in lbs) >= 1000:
                data[name] = (items, truth, headline_articles(truth, items))

        def where(groups, items):
            return {items[i]["url"]: k for k, g in enumerate(groups) for i in g}

        rules = {n: cluster.by_rules(items) for n, (items, _, _) in data.items()}
        meaning = {(n, c): cluster.by_meaning(items, c) for n, (items, _, _) in data.items() for c in CUTS}
        pooled = {"all": [], "headline": []}       # per held-out story: [(rules p, r), ...], [(meaning p, r), ...]
        print(f"{'held out':16} {'cut':>4}   {'all articles: rules → meaning':32} {'headline-size: rules → meaning':34}"
              f" {'stories':>14} {'3+ outlets':>12}")
        for held, (items, truth, big) in data.items():
            train = [n for n in data if n != held]
            cut = max(CUTS, key=lambda c: f1([pr for n in train for pr in
                                               bcubed_parts(data[n][1], where(meaning[(n, c)], data[n][0])).values()]))
            a = bcubed_parts(truth, where(rules[held], items))
            b = bcubed_parts(truth, where(meaning[(held, cut)], items))
            for scope, keep in (("all", None), ("headline", big)):
                stories: dict[object, list] = {}
                for u in a:
                    if keep is None or u in keep:
                        stories.setdefault(truth[u], ([], []))
                        stories[truth[u]][0].append(a[u])
                        stories[truth[u]][1].append(b[u])
                pooled[scope] += list(stories.values())
            ra, rb = (sizes(groups, items) for groups in (rules[held], meaning[(held, cut)]))
            hb = [b[u] for u in b if u in big]
            ha = [a[u] for u in a if u in big]
            print(f"{held:16} {cut:4}   {f1(list(a.values())):.0%} → {f1(list(b.values())):.0%}{'':22}"
                  f" {f1(ha):.0%} → {f1(hb):.0%}{'':24} {ra[0]:5} → {rb[0]:<5} {ra[1]:4} → {rb[1]:<4}")
        rng = random.Random(0)
        for scope, stories in pooled.items():
            base = f1([pr for s in stories for pr in s[1]]) - f1([pr for s in stories for pr in s[0]])
            diffs = []
            for _ in range(2000):
                sample = [rng.choice(stories) for _ in stories]
                diffs.append(f1([pr for s in sample for pr in s[1]]) - f1([pr for s in sample for pr in s[0]]))
            diffs.sort()
            print(f"{scope:9} meaning − rules: {base:+.1%}  (95% interval {diffs[50]:+.1%} to {diffs[1949]:+.1%})")
        items = max((d[0] for d in data.values()), key=len)
        runs = (("rules", lambda: cluster.by_rules(items)), ("meaning", lambda: cluster.by_meaning(items, 0.55)))
        for label, run in runs:
            run()
            t = time.time()
            run()
            print(f"{label:8} {len(items)} articles: {time.time() - t:.1f}s (repeat fetch)")


if __name__ == "__main__":
    main()
