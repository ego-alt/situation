"""Learning from labels (learn.py), the story questions that feed it
(labels.py), and the map's local times and country cards."""
import random

import numpy as np
import pytest

import embed
import labels
import learn
from models import Label
from tests.conftest import NOW


def _story_answer(q, **answer):
    return {**q, "answer": "checked", "out": [], "in": [], "where": q["place"]["guess"] or "none", **answer}


def test_a_story_question_answers_many_pairs_and_a_place(loaded, app):
    client = app.test_client()
    q = labels.story_question(labels.current_snapshot(NOW), random.Random(2))
    assert q["kind"] == "story" and len(q["members"]) >= 2 and q["place"]["options"]
    members, nearby = [m["url"] for m in q["members"]], [m["url"] for m in q["nearby"]]
    bad = [_story_answer(q, out=["https://example.com/not-shown"]), _story_answer(q, where="XX"),
           _story_answer(q, **{"in": members[:1]})]
    for body in bad:
        assert client.post("/api/label", json=body).status_code == 400
    body = _story_answer(q, out=members[-1:], **{"in": nearby[:1]})
    assert client.post("/api/label", json=body).status_code == 200
    label = Label.query.one()
    kept = members[:-1] + nearby[:1]
    assert labels.story_urls(label) == kept
    pairs = labels.judged_pairs(label)
    assert sum(same for *_, same in pairs) == len(kept) * (len(kept) - 1) // 2
    assert sum(not same for *_, same in pairs) == (1 + len(nearby) - 1) * len(kept)
    # The same story isn't asked again.
    again = [labels.story_question(labels.current_snapshot(NOW), random.Random(k)) for k in range(5)]
    assert all(x is None or x["members"][0]["url"] != members[0] for x in again)


def test_answers_that_agree_score_perfectly_and_train(loaded, app):
    name = labels.current_snapshot(NOW)
    rng = random.Random(5)
    for _ in range(8):
        q = labels.story_question(name, rng)
        if q:
            labels.record(_story_answer(q))
    found = labels.evaluate(Label.query.all())
    assert found["precision"] == 1 and found["recall"] == 1 and found["place_accuracy"] == 1
    data = labels.training_data(Label.query.all())
    assert data["pairs"] and data["place"]
    import events
    model = events.fit(data["pairs"], data["meaning"])
    assert model and len(model["weights"]) == len(model["features"])
    assert learn.fit_place(data["place"])


def test_logistic_regression_learns_a_line():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(400, 2))
    y = (x[:, 0] - 0.5 * x[:, 1] > 0).astype(float)
    model = learn.fit_logistic(x, y)
    assert ((learn.predict_logistic(model, x) > 0.5) == y).mean() > 0.95
    assert model["weights"][0] > 0 > model["weights"][1]


def test_ranking_weights_come_back_from_answers():
    """Answers made with Wikipedia worth three outlets and a lead worth nothing."""
    rng = random.Random(0)
    truth = [1.0, 0.0, 3.0, 1.0]
    examples = []
    for _ in range(300):
        a = [rng.uniform(1, 8), rng.randint(0, 3), rng.random() < 0.3, rng.random() < 0.2]
        b = [rng.uniform(1, 8), rng.randint(0, 3), rng.random() < 0.3, rng.random() < 0.2]
        score = lambda f: sum(w * x for w, x in zip(truth, f, strict=True))  # noqa: E731
        examples.append((a, b, score(a) > score(b)))
    w = learn.fit_rank(examples)["weights"]
    assert w["breadth"] == 1 and w["lead"] < 0.3 and 2 < w["itn"] < 5


def test_a_place_model_can_learn_that_actors_are_not_places():
    """"Washington warns Iran": with the hand rule the US can win on mentions;
    trained on answers that say Iran, the actor's weight goes down."""
    story = [{"title": "Washington warns Tehran over strikes", "summary": "The White House said Iran must stop."},
             {"title": "US warns Iran", "summary": "Washington and the Pentagon issued a statement on Iran."}]
    isos, x = learn.place_candidates(story)
    assert "IR" in isos and "US" in isos
    model = learn.fit_place([(x, isos.index("IR"))] * 20)
    assert learn.choose_place(model, story) == "IR"
    assert learn.choose_place(model, [{"title": "Protests in Paris", "summary": ""}]) == "FR"


def test_grouping_with_a_pair_model(loaded, app):
    """A made-up model that mostly trusts word likeness, standing in for a
    trained one: every article lands in exactly one group, biggest first, and
    the riots still form a story across several outlets."""
    from cluster import cluster
    from tests.conftest import snapshot_items
    model = {"features": ["likeness", "event_likeness", "other_country"], "embed": False,
             "mean": [0.0, 0.0, 0.0], "scale": [0.1, 0.1, 1.0], "weights": [1.2, 0.6, -2.0], "bias": -3.0}
    items, _ = snapshot_items()
    with learn.using({"pairs": model}):
        groups = [[items[i] for i in g] for g in cluster(items)]
    riots = max((g for g in groups if any("macron" in a["title"].lower() for a in g)), key=len)
    assert len({a["outlet"] for a in riots}) >= 4
    assert all(len(g) >= len(h) for g, h in zip(groups, groups[1:], strict=False))
    assert sorted(i for g in cluster(items) for i in g) == list(range(len(items)))


def test_train_compares_saves_and_the_app_uses_it(loaded, app, tmp_path):
    name = labels.current_snapshot(NOW)
    rng = random.Random(7)
    for _ in range(8):
        q = labels.story_question(name, rng)
        if q:
            labels.record(_story_answer(q))
    app.config["MODELS_DIR"] = str(tmp_path / "models")
    runner = app.test_cli_runner()
    result = runner.invoke(args=["train", "--holdout", "0", "--only", "pairs,place"])
    assert result.exit_code == 0, result.output
    assert "hand" in result.output and "learned" in result.output
    assert not (tmp_path / "models").exists()                     # nothing changes without --save
    result = runner.invoke(args=["train", "--save", "--only", "place"])
    assert result.exit_code == 0 and (tmp_path / "models" / "place.json").exists()
    assert learn.load("place") and not learn.load("pairs")
    assert "trained models for place" in runner.invoke(args=["evaluate"]).output
    assert "hand rules" in runner.invoke(args=["evaluate", "--hand"]).output


@pytest.mark.skipif(not embed.available(), reason="the embedding model isn't here")
def test_meaning_likeness_sees_past_different_words():
    v = embed.encode(["Riots spread across France after police shooting",
                      "French unrest continues for a third night", "Wildfire near an Alberta army base"])
    assert v[0] @ v[1] > 0.3 > v[0] @ v[2]


def test_local_time_where_it_happens(loaded, app):
    import geo
    assert geo.timezone("FR") == "Europe/Paris"
    assert geo.timezone("US", 34.05, -118.24) == "America/Los_Angeles"
    assert geo.timezone("US") == "America/New_York"                     # the capital's
    assert geo.timezone("RU", 43.1, 131.9) == "Asia/Vladivostok"
    assert geo.timezone("HK") == "Asia/Hong_Kong"
    stories = app.test_client().get("/api/situation").get_json()["stories"]
    france = next(s for s in stories if s["iso"] == "FR")
    assert france["zone"] == "Europe/Paris"


def test_country_card(loaded, app):
    client = app.test_client()
    card = client.get("/api/country/fr").get_json()
    assert [card[k] for k in ("name", "capital", "leader", "zone")] == ["France", "Paris", "Macron", "Europe/Paris"]
    assert card["stories_30d"] >= 1 and card["last"]["headline"]
    assert client.get("/api/country/XX").status_code == 404


def test_claude_groupings_import_check_and_train(loaded, app, tmp_path):
    import json
    import sys
    sys.path.insert(0, "tools")
    import claude_label

    from tests.conftest import SNAPSHOT
    snap = json.loads(SNAPSHOT.read_text())
    snap = {"taken": NOW.isoformat(), "items": snap["items"], "itn": snap["itn"]}
    items, _, _ = labels.read_snapshot(snap)
    found = claude_label.bundles(items)
    assert found and all(2 <= len(b) <= claude_label.MAX_BUNDLE for b in found)
    riots = next(b for b in found if sum("macron" in items[i]["title"].lower() for i in b) >= 3)
    # Claude's answer, checked: every article once, places known or "none".
    arts = [items[i] for i in riots]
    assert claude_label.parse({"stories": [{"articles": [1], "place": "fr"}]}, len(arts)) is None   # some left out
    everything = list(range(1, len(arts) + 1))
    parsed = claude_label.parse({"stories": [{"articles": everything, "place": "fr", "also": ["it", "XX"]}]}, len(arts))
    assert parsed == ([list(range(len(arts)))], ["FR"], [["IT"]])
    assert "[1]" in claude_label.prompt(arts) and "Macron" in claude_label.prompt(arts)

    name = "2026-10-18T1200"
    urls = [a["url"] for a in arts]
    made = claude_label.label(name, urls, [urls], ["FR"], [["IT"]], "claude-test")
    data = {"snapshots": {name: snap}, "labels": [made]}
    path = tmp_path / "labels.json"
    path.write_text(json.dumps(data))
    runner = app.test_cli_runner()
    assert "1 labels added" in runner.invoke(args=["import-labels", str(path)]).output
    assert "0 labels added" in runner.invoke(args=["import-labels", str(path)]).output        # twice: nothing more
    grouping = Label.query.filter_by(kind="grouping").one()
    assert grouping.source == "claude:claude-test"
    assert len(labels.judged_pairs(grouping)) == len(arts) * (len(arts) - 1) // 2
    assert labels.counts(NOW)["total"] == 0                      # Claude's labels aren't yours

    # A check of Claude's story, answered by you, agrees with it.
    q = labels.check_question(random.Random(1))
    assert q["by"] == "claude" and q["snapshot"] == name and q["place"]["guess"] == "FR"
    labels.record(_story_answer(q))
    agree = labels.claude_agreement(Label.query.all())
    assert agree["agreement"] == 1 and agree["place_agreement"] == 1
    assert labels.check_question(random.Random(1)) is None           # its only story is checked

    out = runner.invoke(args=["train", "--holdout", "0", "--only", "pairs,situations,place"]).output
    assert "training on 2 labels" in out and "stage 2:" in out and "B-cubed" in out
    assert "Claude vs you" in runner.invoke(args=["evaluate"]).output


def test_would_you_show_it(loaded, app):
    client = app.test_client()
    q = labels.show_question(labels.current_snapshot(NOW), random.Random(4))
    assert q["kind"] == "show" and q["story"]["headline"]
    assert client.post("/api/label", json={**q, "answer": "maybe"}).status_code == 400
    assert client.post("/api/label", json={**q, "answer": "yes"}).status_code == 200
    r = labels.evaluate(Label.query.all())
    assert r["bar_stories"] == 1 and r["bar_agreement"] in (0, 1)
    name = labels.current_snapshot(NOW)
    asked = {labels.show_question(name, random.Random(k))["story"]["headline"] for k in range(6)}
    assert q["story"]["headline"] not in asked


def test_the_mix_is_mostly_what_only_you_can_answer(loaded, app):
    kinds = [labels.next_question(NOW, random.Random(k))["kind"] for k in range(40)]
    assert kinds.count("rank") + kinds.count("show") > 0.6 * len(kinds) and "story" in kinds


def test_the_second_pass_joins_a_situation_across_bundles():
    """Two bundles' stories and a lone article, joined as one situation: one label
    spanning them, so the pairs across them are judged; an untouched bundle stays."""
    import sys
    sys.path.insert(0, "tools")
    import claude_label
    found = [{"urls": ["a1", "a2"], "place": "UA", "also": [], "bundle": "b1"},
             {"urls": ["a3"], "place": "UA", "also": ["RU"], "bundle": "b1"},
             {"urls": ["c1", "c2"], "place": "UA", "also": [], "bundle": "b2"},
             {"urls": ["d1", "d2"], "place": "FR", "also": [], "bundle": "b3"},
             {"urls": ["e1"], "place": None, "also": [], "bundle": None},
             {"urls": ["f1"], "place": None, "also": [], "bundle": None}]
    made = claude_label.merge("2026-10-18T1200", [], found, [[0, 2, 4]], "m")
    assert len(made) == 2
    joined = next(lb for lb in made if "c1" in lb["a"]["bundle"])
    assert sorted(joined["a"]["bundle"]) == ["a1", "a2", "a3", "c1", "c2", "e1"]
    assert sorted(map(sorted, joined["a"]["groups"])) == [["a1", "a2", "c1", "c2", "e1"], ["a3"]]
    war = next(n for n, g in enumerate(joined["a"]["groups"]) if "c1" in g)
    assert joined["b"]["places"][war] == "UA"
    assert all("f1" not in lb["a"]["bundle"] for lb in made)        # a lone article no one took in: not judged


def test_bcubed_counts_every_article_once():
    """Truth: {a b c d} and {e}. Found: {a b} {c d e}. a, b: precision 1, recall
    2/4; c, d: precision 2/3, recall 2/4; e: precision 1/3, recall 1. f has no
    true group: it isn't counted."""
    truth = {u: 0 for u in "abcd"} | {"e": 1}
    p, r, n = labels.bcubed(truth, {"a": 0, "b": 0, "c": 1, "d": 1, "e": 1, "f": 1})
    assert n == 5
    assert round(p / n, 4) == round((1 + 1 + 2 / 3 + 2 / 3 + 1 / 3) / 5, 4)
    assert round(r / n, 4) == round((0.5 + 0.5 + 0.5 + 0.5 + 1) / 5, 4)


def test_stage_two_joins_one_situations_stories(loaded, app):
    """A made-up stage-2 model that trusts the same country and the same
    "Ongoing" item: France's riot stories, split into several events by the
    hand rules, come together; every article stays in exactly one story."""
    import cluster
    import situations
    from tests.conftest import snapshot_items
    items, _ = snapshot_items()
    from collections import Counter

    import events
    stage1 = cluster.by_rules(items)
    ctx = events.Articles(items, meaning=False)

    def french(groups):
        return [g for g in groups if events.main_country(sum((cluster.where(items[i]) for i in g), Counter())) == "FR"]
    model = {"features": situations.FEATURES, "embed": False, "mean": [0.0] * len(situations.FEATURES),
             "scale": [1.0] * len(situations.FEATURES), "bias": -2.0,
             "weights": [0, 2, 1, 0.5, 2, 1, 3, -3, -2, -0.2, 0, 0]}
    joined = situations.join(stage1, model, ctx, ["2026 French unrest"])
    assert len(french(joined)) < len(french(stage1))
    assert sorted(i for g in joined for i in g) == list(range(len(items)))
    assert all(len(g) >= len(h) for g, h in zip(joined, joined[1:], strict=False))
