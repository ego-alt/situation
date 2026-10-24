"""Off-the-shelf groupings, with no labels, against ours. Run on the Mac.

    uv run python -I tools/baselines.py <copy of situation.db> <snapshots dir> <claude labels .json.gz>

  hdbscan        density-based clustering of the articles' meaning vectors
                 (scikit-learn's HDBSCAN): the usual off-the-shelf choice for
                 news embeddings (BERTopic uses it)
  agglomerative  average linkage on meaning alone, cut at a distance threshold

Each one's dial is tuned on the training snapshots (best B-cubed F1) and
scored on the held-out snapshot, as our models are. On the October 2026
labels (B-cubed F1 over whole snapshots): HDBSCAN 72%, agglomerative 77%,
ours 81% (stage 1 alone 77%); agglomerative is about twice as fast.
"""
import gzip
import json
import pathlib
import shutil
import sys
import tempfile
from contextlib import contextmanager

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    _, snaps_dir, claude_file = sys.argv[1:4]
    scratch = pathlib.Path(tempfile.mkdtemp())
    shutil.copytree(snaps_dir, scratch / "snapshots")
    from app import create_app
    app = create_app({"TESTING": True, "FETCH_LOOP": False, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{scratch / 'x.db'}",
                      "SNAPSHOTS_DIR": str(scratch / "snapshots"), "MODELS_DIR": str(scratch / "models")})
    with app.app_context():
        import numpy as np
        from sklearn.cluster import HDBSCAN, AgglomerativeClustering

        import embed
        import labels
        import learn
        from labels import evaluate, fit_models, import_labels, judged_pairs
        from models import Label
        with gzip.open(claude_file, "rt", encoding="utf-8") as f:
            import_labels(json.load(f))
        claude = Label.query.all()
        by_snap: dict[str, list] = {}
        for lb in claude:
            by_snap.setdefault(lb.snapshot, []).append(lb)
        folds = [s for s, lbs in sorted(by_snap.items()) if sum(len(judged_pairs(lb)) for lb in lbs) >= 1000]

        def groups_of(assign: np.ndarray) -> list[list[int]]:
            out: dict[int, list[int]] = {}
            for i, c in enumerate(assign.tolist()):
                out.setdefault(c if c >= 0 else -1 - i, []).append(i)      # HDBSCAN's noise: each alone
            return sorted(out.values(), key=len, reverse=True)

        def hdbscan(size):
            return lambda vectors: groups_of(HDBSCAN(min_cluster_size=size, min_samples=1, copy=True)
                                             .fit(vectors).labels_)

        def agglomerative(cut):
            return lambda vectors: groups_of(AgglomerativeClustering(
                n_clusters=None, metric="cosine", linkage="average", distance_threshold=cut).fit(vectors).labels_)

        @contextmanager
        def grouping(method):
            """evaluate() groups each snapshot with this instead of cluster.cluster."""
            real = labels.clustering.cluster
            labels.clustering.cluster = lambda items, ongoing=None: method(embed.encode([embed.text(a) for a in items]))
            try:
                yield
            finally:
                labels.clustering.cluster = real

        dials = {"hdbscan": (hdbscan, [3, 5, 8]),
                 "agglomerative": (agglomerative, [0.5, 0.55, 0.6, 0.65, 0.7])}
        totals: dict[str, list] = {}
        for held in folds:
            train = [lb for lb in claude if lb.snapshot != held]
            print(f"held out {held}:")
            for name, (make, values) in dials.items():
                best = None
                for v in values:
                    with grouping(make(v)), learn.using({}):
                        f1 = evaluate(train)["b3_f1"] or 0
                    if best is None or f1 > best[0]:
                        best = (f1, v)
                with grouping(make(best[1])), learn.using({}):
                    r = evaluate(by_snap[held])
                totals.setdefault(name, []).append(r)
                print(f"    {name:14} (dial {best[1]})  B-cubed P {r['b3_precision']:.0%} R {r['b3_recall']:.0%} "
                      f"F1 {r['b3_f1']:.0%}")
            ours = fit_models(train, ["pairs", "situations"])
            for name, chosen in (("ours (both stages)", ours), ("ours (stage 1 only)", {"pairs": ours["pairs"]})):
                with learn.using(chosen):
                    r = evaluate(by_snap[held])
                totals.setdefault(name, []).append(r)
                print(f"    {name:26} B-cubed P {r['b3_precision']:.0%} R {r['b3_recall']:.0%} F1 {r['b3_f1']:.0%}")
        print("\naveraged:")
        for name, rs in totals.items():
            mean = {k: sum(x[k] for x in rs) / len(rs) for k in ("b3_precision", "b3_recall", "b3_f1")}
            print(f"    {name:20} B-cubed P {mean['b3_precision']:.0%} R {mean['b3_recall']:.0%} "
                  f"F1 {mean['b3_f1']:.0%}")


if __name__ == "__main__":
    main()
