import json
import pathlib
from datetime import UTC, datetime

import pytest

import pipeline
from app import create_app
from models import db
from sources import BY_KEY

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SNAPSHOT = FIXTURES / "snapshot-2026-10-18.json"
NOW = datetime(2026, 10, 19, 2, 0, tzinfo=UTC)
# A second real day, so the tuning can't fit one day only: the morning after,
# taken from the live app (Macron's address, the Wainwright fire, Albania).
SNAPSHOT_2 = FIXTURES / "snapshot-2026-10-19.json"
NOW_2 = datetime(2026, 10, 19, 11, 40, tzinfo=UTC)
# A third, from the live app with all 17 feeds: two stories that share names
# (the Wainwright fire and Alberta's referendum) and the riots' third day.
SNAPSHOT_3 = FIXTURES / "snapshot-2026-10-20.json"


def snapshot_items(path=SNAPSHOT):
    """A real snapshot, deduplicated, as the fetcher returns it."""
    data = json.loads(path.read_text())
    seen, items = set(), []
    for i in data["items"]:
        if i["url"] in seen:
            continue
        seen.add(i["url"])
        i["outlet"] = BY_KEY[i["source"]].outlet
        i["published"] = datetime.fromisoformat(i["published"]) if i["published"] else None
        items.append(i)
    return items, data["itn"]


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """The snapshots are from October 2026: "today" is the morning after."""
    monkeypatch.setattr(pipeline, "clock", lambda: NOW)
    return NOW


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "FETCH_LOOP": False,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
                      "SNAPSHOTS_DIR": str(tmp_path / "snapshots"),
                      "MODELS_DIR": str(tmp_path / "models")})   # the hand rules, not models saved on this machine
    with app.app_context():
        yield app
        db.session.remove()


@pytest.fixture
def loaded(app):
    """The app after one fetch of the snapshot."""
    from pipeline import run
    items, itn = snapshot_items()
    run(lambda: items, lambda: itn, NOW)
    return app
