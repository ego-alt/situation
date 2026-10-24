"""Which way of grouping agrees best with the labels? Run on the Mac, never the Pi.

    uv run python -I tools/compare_grouping.py <copy of situation.db> <its snapshots dir> <claude labels .json.gz>

Puts everything in a scratch database (the copy and the Pi are untouched),
then for each of Claude's bigger snapshots in turn trains on Claude's other
snapshots and tests on that one (so no model is scored on articles it learned
from).

Variants: the hand rules; stage 1 (the pair model, two passes, correlation
clustering); stage 2 (situations) on the hand rules; and both stages. Prints
B-cubed (per article) and pair precision, recall and F1 against Claude's labels.
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

        import embed
        import learn
        from labels import evaluate, import_labels, judged_pairs
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
            import situations
            from labels import fit_models, situation_data
            stage1 = fit_models(train, ["pairs"])
            both = fit_models(train, ["pairs", "situations"])
            hand_then = situations.fit(situation_data(train, None))
            return {"hand rules": {},
                    "stage 1 (pairs, two passes)": {"pairs": stage1["pairs"]},
                    "hand rules + stage 2": {"situations": hand_then} if hand_then else {},
                    "stage 1 + stage 2": both}

        def scores(models, test):
            out = {}
            for name, chosen in models.items():
                with learn.using(chosen):
                    out[name] = evaluate(test)
            return out

        def line(name, r):
            return (f"    {name:28} B-cubed P {_pct(r['b3_precision'])} R {_pct(r['b3_recall'])} "
                    f"F1 {_pct(r['b3_f1'])}   pairs P {_pct(r['precision'])} R {_pct(r['recall'])} F1 {_pct(r['f1'])}")

        runs: dict[str, list] = {}
        for held in folds:
            train = [lb for lb in claude if lb.snapshot != held]
            print(f"held out {held}:")
            for name, r in scores(variants(train), by_snap[held]).items():
                runs.setdefault(name, []).append(r)
                print(line(name, r))
        print("\naveraged over the held-out snapshots:")
        for name, rs in runs.items():
            mean = {k: sum(x[k] or 0 for x in rs) / len(rs)
                    for k in ("b3_precision", "b3_recall", "b3_f1", "precision", "recall", "f1")}
            print(line(name, mean))


def _pct(x):
    return "  —" if x is None else f"{x:4.0%}"


if __name__ == "__main__":
    main()
