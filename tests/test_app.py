"""The stored pipeline, the page's data, and the brief."""
import json
import pathlib
import re
import threading
import time
from datetime import timedelta

from sqlalchemy import DateTime

import app as app_module
import labels
import page
import pipeline
from fetch import parse_itn, web_link
from models import Article, DayTally, ItnSnapshot, Label, Page, SourceStatus, Story, db
from ongoing import about_situation
from pipeline import _close, regroup, save_itn, source_health, store
from projection import HEIGHT, WIDTH, project
from score import BACKGROUND, MAJOR
from tests.conftest import NOW, NOW_2, SNAPSHOT_2, snapshot_items


def test_snapshot_shows_only_what_matters(loaded):
    snap = page.build(NOW)
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
    text = page.brief(page.build(NOW))
    assert text.count("\n") <= 4 and text.startswith("▲")
    assert page.brief({"stories": []}).startswith("Quiet day")


def test_api(loaded, app):
    res = app.test_client().get("/api/situation")
    body = res.get_json()
    assert res.status_code == 200 and body["counts"]["major"] == 3 and body["sources"]["total"] == 17


def test_the_page_is_made_up_after_a_fetch_and_served_as_kept(loaded, app, monkeypatch):
    monkeypatch.setattr(app_module, "fetch_all", lambda report: report.update(bbc=(5, None)) or [])
    monkeypatch.setattr(app_module, "fetch_itn", lambda: {"items": [], "ongoing": []})
    monkeypatch.setattr(app_module.signals, "fetch", lambda now: 0)
    client = app.test_client()
    first = client.get("/api/situation")
    assert client.get("/api/situation", headers={"If-None-Match": first.headers["ETag"]}).status_code == 304
    SourceStatus.record({"nyt": (9, None)}, NOW)
    assert client.get("/api/situation").data == first.data          # not made up again for each reader
    assert first.get_json()["fetched"] is None
    app_module.fetch_once()
    after = client.get("/api/situation", headers={"If-None-Match": first.headers["ETag"]})
    assert after.status_code == 200 and after.get_json()["fetched"] == NOW.isoformat()



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
    page = app.test_client().get("/dispatch/2026-11-12", headers={"X-Forwarded-Prefix": "/situation"})
    assert b'href="/situation/static/css/dispatch.css' in page.data and b"js/woodcut.js" in page.data


# ---------- what the audit asked for ----------

def test_only_web_links_become_links():
    assert web_link(" https://www.bbc.co.uk/news/1 ") == "https://www.bbc.co.uk/news/1"
    assert web_link("javascript:alert(document.cookie)") is None
    assert web_link("data:text/html,<script>") is None and web_link(None) is None


def test_in_the_news_is_kept_only_when_it_changes(app):
    itn = {"items": ["A town hall burns down in France."], "ongoing": ["Iran war"]}
    save_itn(itn, NOW)
    save_itn(itn, NOW)
    save_itn({**itn, "ongoing": ["Iran war", "Yemen offensives"]}, NOW)
    assert ItnSnapshot.query.count() == 2


def test_stories_close_instead_of_vanishing(loaded, monkeypatch):
    """Two days later, today's stories are closed with their ids, and off the list."""

    shown = {s["id"]: s["headline"] for s in page.build(pipeline.clock())["stories"]}
    later = NOW + timedelta(days=3)
    monkeypatch.setattr(pipeline, "clock", lambda: later)
    regroup(later)
    assert Story.query.filter_by(closed=False).count() == 0
    assert Story.query.filter_by(tier="background").count() == 0      # never shown: not kept
    assert page.build(pipeline.clock())["stories"] == []
    assert {s.id: s.headline for s in Story.query.filter(Story.id.in_(shown))} == shown


def test_a_silent_feed_is_flagged(app):
    SourceStatus.record({"bbc": (0, "HTTP 404"), "nyt": (40, None)}, NOW - timedelta(hours=7))
    SourceStatus.record({"bbc": (0, "HTTP 404"), "nyt": (38, None)}, NOW)
    health = source_health(NOW)
    assert health == {"ok": 16, "total": 17, "silent": ["BBC"]}       # the 12 never tried don't count against


def test_health_fails_once_no_feed_has_answered_for_an_hour(app, monkeypatch):
    client = app.test_client()
    assert client.get("/healthz").status_code == 200                  # just started: give it time
    monkeypatch.setattr(app_module, "STARTED", time.monotonic() - 7200)
    assert client.get("/healthz").status_code == 503                  # up two hours, nothing fetched
    SourceStatus.record({"bbc": (30, None)}, NOW - timedelta(hours=2))
    SourceStatus.record({"signals:usgs": (4, None), "papers:nature": (12, None)}, NOW)    # not the news
    assert client.get("/healthz").status_code == 503
    SourceStatus.record({"bbc": (30, None)}, NOW - timedelta(minutes=10))
    assert client.get("/healthz").get_json()["ok"] is True


def test_the_second_day_is_capped_at_ten(app, monkeypatch):
    monkeypatch.setattr(pipeline, "clock", lambda: NOW_2)
    items, itn = snapshot_items(SNAPSHOT_2)
    pipeline.run(lambda: items, lambda: itn, NOW_2)
    snap = page.build(pipeline.clock())
    assert len(snap["stories"]) == 10 and snap["cleared"] > 10
    assert snap["stories"][0]["iso"] == "FR" and snap["stories"][0]["tier"] == "major"
    # A day on, the Pope and the Houthis make way for fresher stories.
    assert {"FR", "CA", "UA", "IL"} <= {s["iso"] for s in snap["stories"]}
    tally = DayTally.query.one()
    assert (tally.cleared, tally.major + tally.headline) == (snap["cleared"], 10)


def test_an_active_situation_without_shared_reports_quotes_nothing(loaded):
    iran = next(o for o in page.build(pipeline.clock())["ongoing"] if o["topic"] == "Iran war")
    assert iran["latest"] is None and iran["singles"] >= 1           # a lone essay isn't the news


def test_a_story_that_faded_is_closed_not_deleted(app):
    """Major once, background by the time it leaves the window: it was on the
    page, so it's kept (closed) and never pushed again."""

    faded, never = Story(headline="", tier=BACKGROUND), Story(headline="", tier=BACKGROUND)
    db.session.add_all([faded, never])
    faded.rank("Quake", MAJOR, 9)
    faded.rank("Quake", BACKGROUND, 1)
    never.rank("Fete", BACKGROUND, 1)
    db.session.flush()
    _close([faded, never])
    db.session.commit()
    assert [(s.headline, s.closed) for s in Story.query] == [("Quake", True)]


def test_only_rank_sets_a_tier():
    root = pathlib.Path(__file__).resolve().parent.parent
    writes = re.compile(r"\.tier\s*(,[^=]*)?=(?!=)")   # story.tier = …, or story.tier, story.score = …
    offenders = [f"{p.name}:{n}" for p in root.glob("*.py") if p.name != "models.py"
                 for n, line in enumerate(p.read_text().splitlines(), 1) if writes.search(line)]
    assert offenders == []


def test_stories_carry_their_places(loaded):
    france = next(s for s in page.build(pipeline.clock())["stories"] if s["iso"] == "FR")
    names = [p["name"] for p in france["spots"]]
    assert {"Paris", "Limoges"} <= set(names) and all(p["iso"] == "FR" or p["n"] >= 2 for p in france["spots"])
    assert all(0 <= p["x"] <= 1000 for p in france["spots"])


def test_an_active_situation_has_a_week_of_places(app, monkeypatch):
    monkeypatch.setattr(pipeline, "clock", lambda: NOW_2)
    items, itn = snapshot_items(SNAPSHOT_2)
    pipeline.run(lambda: items, lambda: itn, NOW_2)
    war = next(o for o in page.build(pipeline.clock())["ongoing"] if o["topic"] == "Russo-Ukrainian war")
    names = [t["name"] for t in war["track"]]
    assert names[0] == "Kyiv" and war["track"][0]["n"] >= 5
    assert all(t["iso"] in {"UA", "RU"} and t["last"] for t in war["track"])


def test_the_zoomed_map_files():
    root = pathlib.Path(__file__).resolve().parent.parent
    fr = json.loads((root / "static/map/regions/FR.json").read_text())
    assert len(fr["regions"]) > 90 and fr["cities"][0]["name"] == "Paris"
    x0, y0, x1, y1 = fr["focus"]
    assert x1 - x0 < 50 and fr["bbox"][2] - fr["bbox"][0] > 400      # the mainland, not French Guiana
    gazetteer = json.loads((root / "data/gazetteer.json").read_text())
    assert ["Pokrovsk", "UA"] in [g[:2] for g in gazetteer]


def test_sync_fetches_now_but_not_twice_in_a_row(app, monkeypatch):
    calls = []

    def fake_fetch(now=None):
        calls.append(1)
        SourceStatus.record({"bbc": (5, None)}, NOW)
    monkeypatch.setattr(app_module, "fetch_once", fake_fetch)
    client = app.test_client()
    assert client.post("/api/sync").get_json()["status"] == "fetched" and len(calls) == 1
    # Moments later: spare the news sites.
    assert client.post("/api/sync").get_json()["status"] == "recent" and len(calls) == 1
    db.session.get(SourceStatus, "bbc").last_ok = (NOW - timedelta(minutes=10))
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


def test_two_making_the_page_up_at_once_keep_the_one_row(app, monkeypatch):
    """A reader arriving before the first fetch is done makes the page up as the fetch loop does."""
    slow = page.build
    monkeypatch.setattr(page, "build", lambda now: time.sleep(0.2) or slow(now))
    failed = []

    def make():
        with app.app_context():
            try:
                page.publish(NOW)
            except Exception as e:  # noqa: BLE001 — whatever it is, it's the failure
                failed.append(e)
    threads = [threading.Thread(target=make) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert failed == [] and db.session.query(Page).count() == 1


def test_each_source_says_when_it_went_out_and_was_updated(app):
    item = {"source": "bbc", "outlet": "bbc", "title": "Storm hits coast", "url": "https://example.org/a",
            "published": NOW - timedelta(hours=3), "updated": NOW - timedelta(hours=3), "position": 0}
    store([item])
    store([{**item, "updated": NOW - timedelta(minutes=30)}])        # revised later: kept
    story = Story(headline="Storm", tier="headline", articles=Article.query.all())
    db.session.add(story)
    db.session.commit()
    source = page._story(story, NOW)["sources"][0]
    assert source["published"].startswith("2026-10-18T23:00") and source["updated"].startswith("2026-10-19T01:30")
    story.articles[0].updated = story.articles[0].published + timedelta(minutes=2)   # stamped alike: no "updated"
    assert page._story(story, NOW)["sources"][0]["updated"] is None


def test_a_running_story_shows_its_latest_development(app):
    def article(outlet, title, hours_ago):
        return Article(url=f"https://example.org/{outlet}/{hours_ago}", source=outlet, outlet=outlet, title=title,
                       published=(NOW - timedelta(hours=hours_ago)))
    arts = [article("bbc", "Riots spread across France", 30), article("nyt", "Riots spread across France", 29),
            article("ap", "Paris police issue overnight curfew in two areas after riots", 1),
            article("reuters", "Riots spread across France again", 0.5), article("dw", "France", 0.2),
            article("cna", "Lavrov backs Iran, hours after Trump says Russia is doing little", 0.1)]
    story = Story(headline="Riots spread across France", tier="major", articles=arts)
    db.session.add(story)
    db.session.commit()
    latest = page._story(story, NOW)["latest"]
    assert latest["title"].startswith("Paris police") and latest["outlet"] == "AP"
    assert page._story(story, NOW + timedelta(hours=8))["latest"] is None      # nothing new for 8 hours


def test_labelling_questions_answers_and_evaluation(loaded, app):
    client = app.test_client()
    body = client.get("/api/label").get_json()
    q = body["question"]
    assert q["kind"] in ("rank", "show") and body["counts"] == {"today": 0, "total": 0}
    answer = {"rank": "a", "show": "yes"}[q["kind"]]
    assert client.post("/api/label", json={**q, "answer": answer}).status_code == 200
    assert client.post("/api/label", json={**q, "answer": "maybe"}).status_code == 400
    assert client.post("/api/label", json={**q, "snapshot": "../../etc/passwd", "answer": "a"}).status_code == 400
    assert Label.query.count() == 1

    # A ranking that agrees with the scores scores perfectly.
    name = q["snapshot"]
    items, itn, taken = labels.load_snapshot(name)
    stories = sorted(labels.assessed(items, itn, taken), key=lambda s: -s[1]["score"])
    Label.query.delete()
    hi, lo = stories[0], stories[5]
    labels.record({"kind": "rank", "snapshot": name, "answer": "a",
                   "a": {"headline": hi[1]["headline"], "urls": [x["url"] for x in hi[0]]},
                   "b": {"headline": lo[1]["headline"], "urls": [x["url"] for x in lo[0]]}})
    r = labels.evaluate(Label.query.all())
    assert (r["rank_pairs"], r["rank_agreement"]) == (1, 1)


def test_only_the_server_runs_the_fetch_loop(monkeypatch):
    """`flask fetch` fetching alongside its own background loop stored the same articles twice."""
    monkeypatch.delenv("WERKZEUG_RUN_MAIN", raising=False)
    monkeypatch.delenv("FLASK_RUN_FROM_CLI", raising=False)
    assert app_module._serving()                                   # gunicorn
    monkeypatch.setenv("FLASK_RUN_FROM_CLI", "true")
    monkeypatch.setattr("sys.argv", ["flask", "--app", "app:create_app", "fetch"])
    assert not app_module._serving()                               # a one-off command
    monkeypatch.setattr("sys.argv", ["flask", "--app", "app:create_app", "run", "--port", "8000"])
    assert app_module._serving()                                   # the dev server


def test_a_situation_takes_stories_about_it_wherever_they_are(app):
    """By subject, not by place: a Russian strike reported from Germany is the war;
    a ban on Russia's ice hockey team, placed in Russia, isn't."""

    def story(headline, iso, titles):
        s = Story(headline=headline, tier="headline", iso=iso, outlets=len(titles), first_seen=NOW)
        s.articles = [Article(url=f"https://x.example/{headline[:9]}{i}", source="ap", outlet=f"o{i}", title=t)
                      for i, t in enumerate(titles)]
        db.session.add(s)
        return s
    war = {"RU", "UA"}
    strike = story("More than 50 trapped after Russian strike on school, Zelenskyy says", "DE",
                   ["Russian strike hits school in Ukraine", "Dozens trapped after strike, Zelenskyy says"])
    hockey = story("Opposition growing to Russia ice hockey reinstatement", "RU",
                   ["Nordic nations refuse to face Russia in ice hockey", "Russia ice hockey ban over Ukraine war"])
    vote = story("Democrats win the House", "US", ["Democrats win the House", "What the vote means for Ukraine aid"])
    assert about_situation(strike, war)
    assert not about_situation(hockey, war)      # not told in the war's words
    assert not about_situation(vote, war)        # one article's aside isn't what the story is about
    vance = story("JD Vance, frustrated by his party's defeat, sparks debate", "US",
                  ["Vance frustrated as Iran war costs Republicans", "Republicans blame the Iran war for defeat"])
    assert not about_situation(vance, {"IR"})    # its articles blame the war; it's about the midterms


def test_every_timestamp_column_is_utc():
    """Stored as UTC and read back with its zone (models.UtcDateTime), so no code
    strips or adds the zone by hand."""

    columns = [(t.name, c) for t in db.metadata.tables.values() for c in t.columns]
    plain = [f"{name}.{c.name}" for name, c in columns if isinstance(c.type, DateTime)]
    assert plain == []
