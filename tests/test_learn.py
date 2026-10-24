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
    model = learn.fit_pairs(data["pairs"], data["meaning"])
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
