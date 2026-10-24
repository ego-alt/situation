"""The ground truth for grouping (groundtruth.py), how groupings are scored
against it (metrics.py), and the tools that build and use it."""
import json
import sqlite3
import sys

import pytest

import cluster
import groundtruth
import metrics
from tests.conftest import NOW, SNAPSHOT, SNAPSHOT_2, snapshot_items

sys.path.insert(0, "tools")


def test_bcubed_counts_every_article_once():
    """Truth: {a b c d} and {e}. Found: {a b} {c d e}. a, b: precision 1, recall
    2/4; c, d: precision 2/3, recall 2/4; e: precision 1/3, recall 1. f has no
    true event: it isn't scored."""
    truth = {u: 0 for u in "abcd"} | {"e": 1}
    parts = metrics.bcubed(truth, {"a": 0, "b": 0, "c": 1, "d": 1, "e": 1, "f": 1})
    s = metrics.summary(parts.values())
    assert s["articles"] == 5
    assert round(s["precision"], 4) == round((1 + 1 + 2 / 3 + 2 / 3 + 1 / 3) / 5, 4)
    assert round(s["recall"], 4) == round((0.5 + 0.5 + 0.5 + 0.5 + 1) / 5, 4)


def test_headline_size_and_the_bootstrap():
    """Headline-size: events three or more outlets carry. A grouping that's
    right beats one that lumps everything, with an interval clear of zero."""
    truth = {f"{e}-{k}": e for e in range(30) for k in range(4)}
    outlet = {u: f"outlet{int(u.split('-')[1]) % (3 if int(u.split('-')[0]) < 10 else 1)}" for u in truth}
    big = metrics.headline_articles(truth, outlet)
    assert big == {u for u in truth if int(u.split("-")[0]) < 10}
    right = metrics.bcubed(truth, dict(truth))
    lumped = metrics.bcubed(truth, {u: 0 for u in truth})
    d, lo, hi = metrics.difference(lumped, right, truth)
    assert d > 0 and lo > 0
    assert metrics.score(truth, dict(truth), outlet)["headline"]["f1"] == 1


def _week(tmp_path, grouping):
    """A ground truth week made from two real snapshots, its events as `grouping` puts them."""
    windows, events = {}, {}
    for k, path in enumerate((SNAPSHOT, SNAPSHOT_2)):
        data = json.loads(path.read_text())
        name = f"2026-10-1{8 + k}T0300"
        items, _ = snapshot_items(path)
        windows[name] = {"taken": NOW.isoformat(), "items": data["items"], "itn": data["itn"]}
        events[name] = [{"urls": [items[i]["url"] for i in g], "place": None} for g in grouping(items)]
    return groundtruth.save("2026-W42", {"made": NOW.isoformat(), "model": "test", "rubric": "test",
                                         "windows": windows, "events": events}, str(tmp_path))


def test_a_week_round_trips_and_is_reviewed(tmp_path):
    _week(tmp_path, cluster.by_rules)
    assert groundtruth.weeks(str(tmp_path)) == ["2026-W42"]
    windows = groundtruth.windows(str(tmp_path))
    assert len(windows) == 2 and all(w.truth and len(w.items) == len(w.truth) for w in windows)
    r = groundtruth.review(windows[0])
    assert r["events"] > 10 and r["headline_size"] >= 1 and 0 < r["top5_share"] < 1
    assert "articles in" in groundtruth.describe(windows[0])


def test_evaluating_groupings_on_the_ground_truth(tmp_path, capsys, monkeypatch):
    """Truth made by the hand rules: the hand rules score perfectly, and the
    tool tests each window in turn while there's one week."""
    import evaluate_grouping
    _week(tmp_path, cluster.by_rules)
    windows = groundtruth.windows(str(tmp_path))
    assert [len(test) for _, test in evaluate_grouping.folds(windows)] == [1, 1]
    monkeypatch.setattr(sys, "argv", ["evaluate_grouping", "--dir", str(tmp_path), "--methods", "rules"])
    evaluate_grouping.main()
    out = capsys.readouterr().out
    assert "each window in turn" in out and "F1 100%" in out


def test_labelling_a_week_cuts_its_windows_from_the_database(loaded, app):
    """Two windows a week, ending Wednesday and Sunday at 03:00 UTC, news only;
    a parsed answer must cover every article once."""
    import label_week
    db = app.config["SQLALCHEMY_DATABASE_URI"].removeprefix("sqlite:///")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT count(*) FROM articles").fetchone()[0] > 100
    found = label_week.windows(db, "2026-W43")
    assert set(found) <= {"2026-10-21T0300", "2026-10-25T0300"}
    assert label_week.last_full_week(NOW.date()) == "2026-W42"
    assert label_week.parse({"stories": [{"articles": [1, 2], "place": "fr"}, {"articles": [3], "place": "none"}]}, 3) \
        == ([[0, 1], [2]], ["FR", "none"])
    assert label_week.parse({"stories": [{"articles": [1], "place": "fr"}]}, 2) is None


@pytest.mark.parametrize("title, news", [
    ("Macron condemns violence as France faces fresh clashes", True),
    ("Opinion: why the riots were always coming", False),
    ("What we know about the Wainwright base fire", False),
    ("Can religion help Democrats win in red states?", False),
    ("Saudi Arabia", False),
    ("Live: Paris bans protests after violent unrest", True),
])
def test_what_counts_as_news(title, news):
    from score import is_news
    assert is_news({"title": title, "url": "https://example.com/world/1"}) is news
    assert not is_news({"title": title, "url": "https://example.com/opinion/1"})
