"""The small models (learn.py), your preference labels (labels.py), the
off-the-shelf grouping, and the map's local times and country cards."""
import random

import numpy as np
import pytest

import embed
import labels
import learn
from models import Label
from tests.conftest import NOW


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


@pytest.mark.skipif(not embed.available(), reason="the embedding model isn't here")
def test_grouping_by_meaning_when_switched_on(loaded, app, tmp_path):
    """instance/models/meaning.json switches grouping from the hand rules to
    average linkage on meaning; every article lands in exactly one story."""
    import cluster
    from tests.conftest import snapshot_items
    items, _ = snapshot_items()
    app.config["MODELS_DIR"] = str(tmp_path / "models")
    assert cluster.cluster(items) == cluster.by_rules(items)
    learn.save("meaning", {"cut": 0.55})
    groups = cluster.cluster(items)
    assert groups == cluster.by_meaning(items, 0.55) != cluster.by_rules(items)
    assert sorted(i for g in groups for i in g) == list(range(len(items)))



def test_rank_questions_answers_and_training(loaded, app, tmp_path):
    """Rankings are asked, recorded, scored and trained on; the questions are
    rankings and "would you show it?" only."""
    client = app.test_client()
    kinds = {labels.next_question(NOW, random.Random(k))["kind"] for k in range(20)}
    assert kinds == {"rank", "show"}
    name = labels.current_snapshot(NOW)
    rng = random.Random(3)
    for _ in range(8):
        q = labels.rank_question(name, rng)
        assert client.post("/api/label", json={**q, "answer": "a" if rng.random() < 0.5 else "b"}).status_code == 200
    assert client.post("/api/label", json={**q, "answer": "maybe"}).status_code == 400
    assert labels.evaluate(Label.query.all())["rank_pairs"] >= 1
    assert len(labels.rank_examples(Label.query.all())) >= 5
    app.config["MODELS_DIR"] = str(tmp_path / "models")
    out = app.test_cli_runner().invoke(args=["train", "--only", "rank"]).output
    assert "rank" in out
