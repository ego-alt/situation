"""Which way of grouping agrees best with the labels? Run on the Mac, never the Pi.

    uv run python -I tools/compare_grouping.py <copy of situation.db> <its snapshots dir> <claude labels .json.gz>

Puts everything in a scratch database (the copy and the Pi are untouched),
then for each of Claude's bigger snapshots in turn trains on Claude's other
snapshots and tests on that one (so no model is scored on articles it learned
from), and finally trains on all of Claude's and tests on your own labels.

Variants: the hand rules; the pair model with average linkage; with
correlation clustering; and each again with the neighbourhood (second pass).
Prints pair precision, recall and F1 (Claude's or your "same story?" answers),
and how well the probabilities are calibrated (said 70%, right 70% of the time?).
"""
import gzip
import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    db_copy, snaps_dir, claude_file = sys.argv[1:4]
    scratch = pathlib.Path(tempfile.mkdtemp())
    shutil.copytree(snaps_dir, scratch / "snapshots")
    from app import create_app
    app = create_app({"TESTING": True, "FETCH_LOOP": False, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{scratch / 'x.db'}",
                      "SNAPSHOTS_DIR": str(scratch / "snapshots"), "MODELS_DIR": str(scratch / "models")})
    with app.app_context():
        import numpy as np

        import embed
        import learn
        from labels import evaluate, import_labels, judged_pairs, training_data
        from models import Label, db
        with gzip.open(claude_file, "rt", encoding="utf-8") as f:
            import_labels(json.load(f))
        con = sqlite3.connect(f"file:{db_copy}?mode=ro", uri=True)
        for kind, snapshot, a, b, answer, at in con.execute("SELECT kind, snapshot, a, b, answer, at FROM labels"):
            db.session.add(Label(kind=kind, snapshot=snapshot, a=json.loads(a), b=json.loads(b), answer=answer,
                                 at=datetime.fromisoformat(at), source="you"))
        db.session.commit()
        claude = Label.query.filter(Label.source != "you").all()
        yours = Label.query.filter_by(source="you").all()
        by_snap: dict[str, list] = {}
        for lb in claude:
            by_snap.setdefault(lb.snapshot, []).append(lb)
        folds = [s for s, lbs in sorted(by_snap.items()) if sum(len(judged_pairs(lb)) for lb in lbs) >= 1000]
        meaning = embed.available()
        print(f"Claude: {len(claude)} bundles over {len(by_snap)} snapshots (testing on {len(folds)}); "
              f"yours: {sum(len(judged_pairs(lb)) for lb in yours)} same-story pairs; meaning likeness "
              f"{'on' if meaning else 'OFF (set EMBED_MODEL_DIR)'}\n")

        def variants(train):
            data = training_data(train)
            avg = learn.fit_pairs(data["pairs"], meaning)
            plain = learn.fit_pairs(data["pairs"], False)
            second = training_data(train, first=avg)
            nb = learn.fit_pairs(second["pairs"], meaning, first=avg)
            return {"hand rules": None,
                    "pairs, average": avg,
                    "pairs, average, no meaning": plain,
                    "pairs, correlation": {**avg, "clustering": "correlation"},
                    "pairs + neighbours, average": nb,
                    "pairs + neighbours, correlation": {**nb, "clustering": "correlation"}}

        def scores(models, test):
            out = {}
            for name, model in models.items():
                with learn.using({"pairs": model} if model else {}):
                    out[name] = evaluate(test)
            return out

        from learn import _columns, predict_logistic
        runs: dict[str, list] = {}
        calibration = []
        for held in folds:
            train = [lb for lb in claude if lb.snapshot != held]
            models = variants(train)
            print(f"held out {held}:")
            for name, r in scores(models, by_snap[held]).items():
                runs.setdefault(name, []).append(r)
                print(f"    {name:34} precision {_pct(r['precision'])}  recall {_pct(r['recall'])}  "
                      f"F1 {_pct(r['f1'])}  ({r['same_pairs']} pairs)")
            test = training_data(by_snap[held])["pairs"]
            model = models["pairs, average"]
            p = predict_logistic(model, np.array([f for f, _ in test])[:, _columns(model)])
            calibration += list(zip(p.tolist(), [y for _, y in test], strict=True))
        print("\naveraged over the held-out snapshots:")
        for name, rs in runs.items():
            mean = {k: sum(x[k] or 0 for x in rs) / len(rs) for k in ("precision", "recall", "f1")}
            print(f"    {name:34} precision {_pct(mean['precision'])}  recall {_pct(mean['recall'])}  "
                  f"F1 {_pct(mean['f1'])}")
        print("\ncalibration of the pair model on held-out pairs (said → was):")
        for lo in np.arange(0, 1, 0.2):
            hit = [y for q, y in calibration if lo <= q < lo + 0.2]
            if hit:
                print(f"    {lo:.1f}–{lo + 0.2:.1f}: {len(hit):5} pairs, {sum(hit) / len(hit):.0%} same story")
        if yours:
            print("\ntrained on all of Claude's, tested on your labels (some of their articles are in Claude's too):")
            for name, r in scores(variants(claude), yours).items():
                print(f"    {name:34} precision {_pct(r['precision'])}  recall {_pct(r['recall'])}  "
                      f"F1 {_pct(r['f1'])}  ({r['same_pairs']} pairs)   rank agreement {_pct(r['rank_agreement'])}")


def _pct(x):
    return "  —" if x is None else f"{x:4.0%}"


if __name__ == "__main__":
    main()
