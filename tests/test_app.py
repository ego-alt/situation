"""The stored pipeline, the page's data, and the brief."""
from app import brief_text, snapshot
from fetch import parse_itn
from models import Story
from pipeline import regroup
from projection import HEIGHT, WIDTH, project
from tests.conftest import NOW


def test_snapshot_shows_only_what_matters(loaded):
    snap = snapshot.__wrapped__() if hasattr(snapshot, "__wrapped__") else snapshot()
    shown = snap["stories"]
    assert 5 <= len(shown) <= 15
    assert shown[0]["tier"] == "major" and shown[0]["sources"]
    assert all(s["x"] is not None for s in shown if s["iso"])
    assert {o["topic"] for o in snap["ongoing"]} >= {"Iran war", "Russo-Ukrainian war"}


def test_story_ids_survive_a_refetch(loaded):
    before = {s.headline: s.id for s in Story.query.filter(Story.tier != "background")}
    regroup(NOW)
    after = {s.headline: s.id for s in Story.query.filter(Story.tier != "background")}
    assert before == after


def test_brief(loaded):
    text = brief_text(snapshot())
    assert text.count("\n") <= 4 and text.startswith("▲")
    assert brief_text({"stories": []}).startswith("Quiet day")


def test_api(loaded, app):
    res = app.test_client().get("/api/situation")
    assert res.status_code == 200 and res.get_json()["counts"]["major"] >= 4


def test_itn_parsing():
    page = ("<ul><li>The <b>town hall</b> burns down (pictured) in France.</li><li>Short</li></ul>"
            "<div class=\"itn-footer\"><b>Ongoing</b>: <ul><li>Iran war</li><li>Russo-Ukrainian war timeline</li></ul>"
            " Recent deaths</div>")
    assert parse_itn(page) == {"items": ["The town hall burns down in France."],
                               "ongoing": ["Iran war", "Russo-Ukrainian war"]}


def test_projection_fits_the_map():
    for lon, lat in ((-180, 0), (180, 0), (0, 90), (0, -60)):
        x, y = project(lon, lat)
        assert -1 <= x <= WIDTH + 1 and -1 <= y <= HEIGHT + 1
