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
    assert 5 <= len(shown) <= 10
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
    body = res.get_json()
    assert res.status_code == 200 and body["counts"]["major"] == 3 and body["sources"]["total"] == 17


def test_itn_parsing():
    page = ("<ul><li>The <b>town hall</b> burns down (pictured) in France.</li>"
            "<li>A city hall is partially destroyed by arson (damage pictured) in Limoges.</li><li>Short</li></ul>"
            "<div class=\"itn-footer\"><b>Ongoing</b>: <ul><li>Iran war</li><li>Russo-Ukrainian war timeline</li></ul>"
            " Recent deaths</div>")
    assert parse_itn(page) == {"items": ["The town hall burns down in France.",
                                         "A city hall is partially destroyed by arson in Limoges."],
                               "ongoing": ["Iran war", "Russo-Ukrainian war"]}


def test_projection_fits_the_map():
    for lon, lat in ((-180, 0), (180, 0), (0, 90), (0, -60)):
        x, y = project(lon, lat)
        assert -1 <= x <= WIDTH + 1 and -1 <= y <= HEIGHT + 1


def test_links_carry_the_stack_prefix(app):
    page = app.test_client().get("/", headers={"X-Forwarded-Prefix": "/situation"})
    assert b'href="/situation/static/css/situation.css' in page.data
    assert b'window.APP_ROOT = "/situation"' in page.data
    assert b"js/signed_out.js" in page.data                        # the stack's "signed out" bar


# ---------- what the audit asked for ----------

def test_only_web_links_become_links():
    from fetch import web_link
    assert web_link(" https://www.bbc.co.uk/news/1 ") == "https://www.bbc.co.uk/news/1"
    assert web_link("javascript:alert(document.cookie)") is None
    assert web_link("data:text/html,<script>") is None and web_link(None) is None


def test_in_the_news_is_kept_only_when_it_changes(app):
    from models import ItnSnapshot
    from pipeline import save_itn
    itn = {"items": ["A town hall burns down in France."], "ongoing": ["Iran war"]}
    save_itn(itn, NOW)
    save_itn(itn, NOW)
    save_itn({**itn, "ongoing": ["Iran war", "Yemen offensives"]}, NOW)
    assert ItnSnapshot.query.count() == 2


def test_stories_close_instead_of_vanishing(loaded, monkeypatch):
    """Two days later, today's stories are closed with their ids, and shown under "Earlier this week"."""
    from datetime import timedelta

    import pipeline
    shown = {s["id"]: s["headline"] for s in snapshot()["stories"]}
    later = NOW + timedelta(days=3)
    monkeypatch.setattr(pipeline, "clock", lambda: later)
    regroup(later)
    assert Story.query.filter_by(closed=False).count() == 0
    assert Story.query.filter_by(tier="background").count() == 0      # never shown: not kept
    snap = snapshot()
    assert snap["stories"] == []
    assert {s["id"]: s["headline"] for s in snap["earlier"]} == shown
    monkeypatch.setattr(pipeline, "clock", lambda: NOW + timedelta(days=9))
    assert snapshot()["earlier"] == []                                 # a week on, out of view


def test_a_silent_feed_is_flagged(app):
    from datetime import timedelta

    from pipeline import record_sources, source_health
    from sources import SOURCES
    record_sources({"bbc": (0, "HTTP 404"), "nyt": (40, None)}, NOW - timedelta(hours=7))
    record_sources({"bbc": (0, "HTTP 404"), "nyt": (38, None)}, NOW)
    health = source_health(SOURCES, NOW)
    assert health == {"ok": 16, "total": 17, "silent": ["BBC"]}       # the 12 never tried don't count against


def test_health_fails_once_no_feed_has_answered_for_an_hour(app, monkeypatch):
    import time
    from datetime import timedelta

    import app as app_module
    from pipeline import record_sources
    client = app.test_client()
    assert client.get("/healthz").status_code == 200                  # just started: give it time
    monkeypatch.setattr(app_module, "STARTED", time.monotonic() - 7200)
    assert client.get("/healthz").status_code == 503                  # up two hours, nothing fetched
    record_sources({"bbc": (30, None)}, NOW - timedelta(hours=2))
    assert client.get("/healthz").status_code == 503
    record_sources({"bbc": (30, None)}, NOW - timedelta(minutes=10))
    assert client.get("/healthz").get_json()["ok"] is True


def test_the_second_day_is_capped_at_ten(app, monkeypatch):
    import pipeline
    from models import DayTally
    from tests.conftest import NOW_2, SNAPSHOT_2, snapshot_items
    monkeypatch.setattr(pipeline, "clock", lambda: NOW_2)
    items, itn = snapshot_items(SNAPSHOT_2)
    pipeline.run(lambda: items, lambda: itn, NOW_2)
    snap = snapshot()
    assert len(snap["stories"]) == 10 and snap["cleared"] > 10
    assert snap["stories"][0]["iso"] == "FR" and snap["stories"][0]["tier"] == "major"
    # A day on, the Pope and the Houthis make way for fresher stories.
    assert {"FR", "CA", "UA", "IL"} <= {s["iso"] for s in snap["stories"]}
    tally = DayTally.query.one()
    assert (tally.cleared, tally.major + tally.headline) == (snap["cleared"], 10)


def test_an_active_situation_without_shared_reports_quotes_nothing(loaded):
    iran = next(o for o in snapshot()["ongoing"] if o["topic"] == "Iran war")
    assert iran["latest"] is None and iran["singles"] >= 1           # a lone essay isn't the news


def test_an_old_database_gains_the_new_column(tmp_path):
    import sqlite3

    from app import create_app
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE stories (id INTEGER PRIMARY KEY, headline VARCHAR NOT NULL, tier VARCHAR NOT NULL, "
                "score FLOAT NOT NULL, outlets INTEGER NOT NULL, leads INTEGER NOT NULL, itn VARCHAR, iso VARCHAR, "
                "place VARCHAR, lat FLOAT, lon FLOAT, x FLOAT, y FLOAT, first_seen DATETIME, newest DATETIME)")
    con.execute("INSERT INTO stories (headline, tier, score, outlets, leads) VALUES ('Old', 'headline', 5, 4, 0)")
    con.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, url VARCHAR NOT NULL UNIQUE, source VARCHAR NOT NULL, "
                "outlet VARCHAR NOT NULL, title VARCHAR NOT NULL, summary VARCHAR, published DATETIME, "
                "position INTEGER, fetched DATETIME)")
    con.commit()
    con.close()
    app = create_app({"TESTING": True, "FETCH_LOOP": False, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{path}"})
    with app.app_context():
        story = Story.query.one()
        assert story.headline == "Old" and story.closed is False
        from models import Article
        assert Article.query.count() == 0 and hasattr(Article, "spots")


def test_stories_carry_their_places(loaded):
    france = next(s for s in snapshot()["stories"] if s["iso"] == "FR")
    names = [p["name"] for p in france["spots"]]
    assert {"Paris", "Limoges"} <= set(names) and all(p["iso"] == "FR" or p["n"] >= 2 for p in france["spots"])
    assert all(0 <= p["x"] <= 1000 for p in france["spots"])


def test_an_active_situation_has_a_week_of_places(app, monkeypatch):
    import pipeline
    from tests.conftest import NOW_2, SNAPSHOT_2, snapshot_items
    monkeypatch.setattr(pipeline, "clock", lambda: NOW_2)
    items, itn = snapshot_items(SNAPSHOT_2)
    pipeline.run(lambda: items, lambda: itn, NOW_2)
    war = next(o for o in snapshot()["ongoing"] if o["topic"] == "Russo-Ukrainian war")
    names = [t["name"] for t in war["track"]]
    assert names[0] == "Kyiv" and war["track"][0]["n"] >= 5
    assert all(t["iso"] in {"UA", "RU"} and t["last"] for t in war["track"])


def test_the_zoomed_map_files():
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    fr = json.loads((root / "static/map/regions/FR.json").read_text())
    assert len(fr["regions"]) > 90 and fr["cities"][0]["name"] == "Paris"
    x0, y0, x1, y1 = fr["focus"]
    assert x1 - x0 < 50 and fr["bbox"][2] - fr["bbox"][0] > 400      # the mainland, not French Guiana
    gazetteer = json.loads((root / "data/gazetteer.json").read_text())
    assert ["Pokrovsk", "UA"] in [g[:2] for g in gazetteer]


def test_sync_fetches_now_but_not_twice_in_a_row(app, monkeypatch):
    import threading
    from datetime import timedelta

    import app as app_module
    from models import SourceStatus, db
    from pipeline import record_sources
    calls = []

    def fake_fetch(now=None):
        calls.append(1)
        record_sources({"bbc": (5, None)})
    monkeypatch.setattr(app_module, "fetch_once", fake_fetch)
    client = app.test_client()
    assert client.post("/api/sync").get_json()["status"] == "fetched" and len(calls) == 1
    # Moments later: spare the news sites.
    assert client.post("/api/sync").get_json()["status"] == "recent" and len(calls) == 1
    db.session.get(SourceStatus, "bbc").last_ok = (NOW - timedelta(minutes=10)).replace(tzinfo=None)
    db.session.commit()
    # A fetch already running (the background loop): the button waits for it instead of starting another.
    app_module._fetch_lock.acquire()
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("status", app_module.sync_now()))
    t.start()
    t.join(0.2)
    assert t.is_alive()
    app_module._fetch_lock.release()
    t.join(2)
    assert result["status"] == "joined" and len(calls) == 1


def test_each_source_says_when_it_went_out_and_was_updated(app):
    from datetime import timedelta

    from pipeline import store
    item = {"source": "bbc", "outlet": "bbc", "title": "Storm hits coast", "url": "https://example.org/a",
            "published": NOW - timedelta(hours=3), "updated": NOW - timedelta(hours=3), "position": 0}
    store([item])
    store([{**item, "updated": NOW - timedelta(minutes=30)}])        # revised later: kept
    from app import _story
    from models import Article, Story, db
    story = Story(headline="Storm", tier="headline", articles=Article.query.all())
    db.session.add(story)
    db.session.commit()
    source = _story(story)["sources"][0]
    assert source["published"].startswith("2026-10-18T23:00") and source["updated"].startswith("2026-10-19T01:30")
    story.articles[0].updated = story.articles[0].published + timedelta(minutes=2)   # stamped alike: no "updated"
    assert _story(story)["sources"][0]["updated"] is None


def test_a_running_story_shows_its_latest_development(app, monkeypatch):
    from datetime import timedelta

    import pipeline
    from app import _story
    from models import Article, Story, db
    def article(outlet, title, hours_ago):
        return Article(url=f"https://example.org/{outlet}/{hours_ago}", source=outlet, outlet=outlet, title=title,
                       published=(NOW - timedelta(hours=hours_ago)).replace(tzinfo=None))
    arts = [article("bbc", "Riots spread across France", 30), article("nyt", "Riots spread across France", 29),
            article("ap", "Paris police issue overnight curfew in two areas after riots", 1),
            article("reuters", "Riots spread across France again", 0.5), article("dw", "France", 0.2),
            article("cna", "Lavrov backs Iran, hours after Trump says Russia is doing little", 0.1)]
    story = Story(headline="Riots spread across France", tier="major", articles=arts)
    db.session.add(story)
    db.session.commit()
    latest = _story(story)["latest"]
    assert latest["title"].startswith("Paris police") and latest["outlet"] == "AP"
    monkeypatch.setattr(pipeline, "clock", lambda: NOW + timedelta(hours=8))   # nothing new for 9 hours
    assert _story(story)["latest"] is None


def test_labelling_questions_answers_and_evaluation(loaded, app):
    import random

    import labels
    from models import Label, db
    client = app.test_client()
    body = client.get("/api/label").get_json()
    q = body["question"]
    assert q["kind"] in ("story", "rank", "show") and body["counts"] == {"today": 0, "total": 0}
    answer = {"story": "checked", "rank": "a", "show": "yes"}[q["kind"]]
    assert client.post("/api/label", json={**q, "answer": answer}).status_code == 200
    assert client.post("/api/label", json={**q, "answer": "maybe"}).status_code == 400
    assert client.post("/api/label", json={**q, "snapshot": "../../etc/passwd", "answer": "a"}).status_code == 400
    assert Label.query.count() == 1

    # Plant answers that agree with the grouping and ranking: they should score perfectly.
    name = q["snapshot"]
    items, itn, taken = labels.load_snapshot(name)
    stories = sorted(labels.assessed(items, itn, taken), key=lambda s: -s[1]["score"])
    big, other = stories[0][0], stories[1][0]
    Label.query.delete()
    rng = random.Random(1)
    for _ in range(5):
        a, b = rng.sample(big, 2)
        a, b, c = labels._article(a), labels._article(b), labels._article(rng.choice(other))
        db.session.add(Label(kind="same", snapshot=name, answer="same", a=a, b=b))      # the old pair question
        db.session.add(Label(kind="same", snapshot=name, answer="different", a=a, b=c))
    hi, lo = stories[0], stories[5]
    labels.record({"kind": "rank", "snapshot": name, "answer": "a",
                   "a": {"headline": hi[1]["headline"], "urls": [x["url"] for x in hi[0]]},
                   "b": {"headline": lo[1]["headline"], "urls": [x["url"] for x in lo[0]]}})
    r = labels.evaluate(Label.query.all())
    assert (r["same_pairs"], r["precision"], r["recall"], r["rank_pairs"], r["rank_agreement"]) == (10, 1, 1, 1, 1)
