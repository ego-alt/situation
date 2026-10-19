import json
import pathlib
from datetime import UTC, datetime

import pytest

from app import create_app
from models import db
from sources import BY_KEY

SNAPSHOT = pathlib.Path(__file__).parent / "fixtures" / "snapshot-2026-10-18.json"
NOW = datetime(2026, 10, 19, 2, 0, tzinfo=UTC)


def snapshot_items():
    """The real 18 Oct 2026 snapshot, deduplicated, as the fetcher returns it."""
    data = json.loads(SNAPSHOT.read_text())
    seen, items = set(), []
    for i in data["items"]:
        if i["url"] in seen:
            continue
        seen.add(i["url"])
        i["outlet"] = BY_KEY[i["source"]].outlet
        i["published"] = datetime.fromisoformat(i["published"]) if i["published"] else None
        items.append(i)
    return items, data["itn"]


@pytest.fixture
def app(tmp_path):
    app = create_app({"TESTING": True, "FETCH_LOOP": False,
                      "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}"})
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
