"""The forecast: which question an edition asks, how it settles, your answers and the record."""
import json
import pathlib
import random
import re
from datetime import UTC, datetime, timedelta

import pytest

import forecast
from models import Article, Guess, Question, db

OUT = datetime(2026, 11, 11, tzinfo=UTC)       # issue 2026-W45 comes out
EDITION = "2026-W45"


def market(id, question, yes, volume, ends="2026-11-14T00:00:00Z", made="2026-10-01T00:00:00Z", **extra):
    return {"id": id, "question": question, "outcomes": '["Yes", "No"]',
            "outcomePrices": json.dumps([str(yes), str(1 - yes)]), "volumeNum": volume, "endDate": ends,
            "createdAt": made, "closed": False, "description": f"{question} Rules.", **extra}


def event(id, title, markets, tags=("elections",)):
    return {"id": id, "title": title, "slug": f"event-{id}", "tags": [{"slug": t} for t in tags], "markets": markets}


ANA = event(1, "Ruritania", [market(10, "Will Ana win?", 0.6, 5e5)])


@pytest.fixture
def polymarket(monkeypatch):
    """Polymarket's market endpoint, answering with whatever the test puts in it."""
    now = {"market": market(10, "Will Ana win?", 0.6, 5e5)}
    monkeypatch.setattr(forecast, "read_market", lambda _: now["market"])
    return now


def ask(events=(ANA,)):
    return forecast.ask(EDITION, OUT, events=list(events), rng=random.Random(0))


def settled(edition, title, state, crowd, subject="elections", closes=OUT):
    q = Question(edition=edition, state=state, asked=closes - timedelta(days=7), market=edition, event=edition,
                 slug=edition, title=title, subject=subject, closes=closes, crowd=crowd, settled=closes)
    db.session.add(q)
    return q


# ---------- choosing ----------

def test_one_market_per_event_the_one_nearest_even():
    e = event(1, "Who wins Ruritania?", [market(10, "Will Ana win?", 0.7, 9e5), market(11, "Will Bo win?", 0.45, 2e5),
                                          market(12, "Will Cy win?", 0.1, 1e6)])
    [c] = forecast.eligible([e], OUT, OUT + timedelta(days=7))
    assert (c["market"], c["crowd"], c["subject"]) == ("11", 0.45, "elections")


def test_what_is_never_asked():
    cases = [
        event(1, "Big game", [market(1, "Will A win?", 0.5, 1e6)], tags=("sports",)),
        event(2, "Bitcoin", [market(2, "Will BTC close up?", 0.5, 1e6)], tags=("crypto", "finance")),
        event(3, "Will the president die in office?", [market(3, "Will he die?", 0.5, 1e6)], tags=("politics",)),
        event(4, "Air strikes", [market(4, "Will country X strike Y?", 0.5, 1e6)], tags=("geopolitics",)),
        event(5, "Highest temperature in London?", [market(5, "Will it be 12C?", 0.5, 1e6)], tags=("weather",)),
        event(6, "Trump's Truth Social posts", [market(6, "Will he post 100+ times?", 0.5, 1e6)], tags=("politics",)),
        event(7, "Sure thing", [market(7, "Will the sun rise?", 0.95, 1e6)]),                 # the crowd is sure
        event(8, "Quiet", [market(8, "Will a small thing happen?", 0.5, 1e4)]),                 # too little traded
        event(9, "Later", [market(9, "Will it happen next month?", 0.5, 1e6, ends="2026-12-01T00:00:00Z")]),
        event(10, "New", [market(10, "Did it start after?", 0.5, 1e6, made="2026-11-12T00:00:00Z")]),
        event(11, "Untagged", [market(11, "Will anything happen?", 0.5, 1e6)], tags=("misc",)),
    ]
    assert forecast.eligible(cases, OUT, OUT + timedelta(days=7)) == []


def test_the_most_traded_wins_but_never_last_weeks_event():
    cands = [{"event": "a", "volume": 9e6}, {"event": "b", "volume": 4e6}, {"event": "c", "volume": 1e6}]
    assert forecast.choose(cands, None, random.Random(1))["event"] == "a"
    assert forecast.choose(cands, "a", random.Random(1))["event"] == "b"
    assert forecast.choose([{"event": "a", "volume": 1}], "a", random.Random(1)) is None


def test_a_tie_is_drawn_at_random():
    cands = [{"event": "a", "volume": 10e6}, {"event": "b", "volume": 9.5e6}, {"event": "c", "volume": 5e6}]
    drawn = {forecast.choose(cands, None, random.Random(seed))["event"] for seed in range(40)}
    assert drawn == {"a", "b"}


def test_the_reading_shares_a_name_with_the_question(app):
    week = OUT - timedelta(days=2)
    for i, (source, title) in enumerate([
            ("ap", "Libertarians could spoil Republicans’ chances in the fight for control of the US Senate"),
            ("bbc", "Republicans fight to keep the Senate as polls tighten"),
            ("reuters", "Republicans fight to keep the Senate as polls tighten"),          # the same headline again
            ("reuters", "Ghana inflation rises less than expected in October"),             # shares only a month
            ("guardian", "Fire safety reforms to be rolled out")]):                         # shares no name
        db.session.add(Article(url=f"https://example.org/{i}", source=source, outlet=source, title=title, fetched=week))
    db.session.add(Article(url="https://example.org/old", source="ap", outlet="ap",
                           title="Senate Republicans, a month ago", fetched=OUT - timedelta(days=30)))
    db.session.commit()
    found = forecast.coverage("Will the Republican Party control the Senate in October?", OUT)
    assert [a["title"][:20] for a in found] == ["Libertarians could s", "Republicans fight to"]
    assert forecast.coverage("Will PPI be 6.8% or more in October?", OUT) == []


# ---------- asking and settling ----------

def test_asked_once_and_kept(app):
    q = ask()
    assert (q.state, q.title, q.crowd, q.slug) == ("open", "Will Ana win?", 0.6, "event-1")
    assert ask([event(2, "Other", [market(20, "Will Bo win?", 0.5, 9e9)])]).title == "Will Ana win?"


def test_nothing_eligible_is_said_and_final(app):
    q = ask([])
    assert q.state == "none"
    with pytest.raises(ValueError):
        forecast._move(q, "open", OUT)


def test_asked_as_its_issue_comes_out_or_not_at_all(app):
    """An issue that came out before there were questions doesn't get one days later."""
    forecast.run(OUT + timedelta(days=2))
    assert db.session.get(Question, EDITION) is None
    shown = forecast.view(EDITION, "ellery", OUT + timedelta(days=2))
    assert (shown["question"], shown["asked"], shown["pending"]) == (None, False, False)


def test_settling(app, polymarket):
    ask()
    assert forecast.settle(OUT + timedelta(days=1)) == 0
    polymarket["market"] = {**polymarket["market"], "closed": True, "umaResolutionStatus": "proposed"}
    assert forecast.settle(OUT + timedelta(days=4)) == 1
    assert db.session.get(Question, EDITION).state == "closed"
    polymarket["market"] = {**polymarket["market"], "umaResolutionStatus": "disputed"}
    assert forecast.settle(OUT + timedelta(days=5)) == 0                                # still waiting, calmly
    polymarket["market"] = {**polymarket["market"], "umaResolutionStatus": "resolved", "outcomePrices": '["1", "0"]'}
    forecast.settle(OUT + timedelta(days=9))
    q = db.session.get(Question, EDITION)
    assert (q.state, q.settled) == ("yes", OUT + timedelta(days=9))


def test_a_refunded_market_is_void(app, polymarket):
    ask()
    polymarket["market"] = {**market(10, "", 0.5, 5e5), "closed": True, "umaResolutionStatus": "resolved"}
    forecast.settle(OUT + timedelta(days=4))
    assert db.session.get(Question, EDITION).state == "void"


def test_only_forecast_changes_a_question_or_a_guess():
    root = pathlib.Path(__file__).resolve().parent.parent
    writes = re.compile(r"\b(Question|Guess)\(|\.state\s*=(?!=)|\.chance\s*=(?!=)")
    offenders = [f"{p.name}:{n}" for p in root.glob("*.py") if p.name not in ("forecast.py", "models.py")
                 for n, line in enumerate(p.read_text().splitlines(), 1) if writes.search(line)]
    assert offenders == []


# ---------- answering ----------

def test_answering_keeps_the_crowd_at_that_moment(app, polymarket):
    ask()
    polymarket["market"] = market(10, "", 0.55, 5e5)
    g = forecast.answer(EDITION, "ellery", 70, OUT + timedelta(days=1))
    assert (g.chance, g.crowd) == (70, 0.55)
    polymarket["market"] = market(10, "", 0.5, 5e5)
    g = forecast.answer(EDITION, "ellery", 75, OUT + timedelta(days=2))
    assert Guess.query.count() == 1 and (g.chance, g.crowd) == (75, 0.5)                # changed, not added


@pytest.mark.parametrize("chance", [-5, 101, 72])
def test_a_chance_is_0_to_100_in_steps(app, polymarket, chance):
    ask()
    with pytest.raises(ValueError):
        forecast.answer(EDITION, "ellery", chance, OUT)


def test_no_answers_once_it_closes(app, polymarket):
    ask()
    with pytest.raises(ValueError):
        forecast.answer(EDITION, "ellery", 50, OUT + timedelta(days=4))                 # it closed on the 14th


# ---------- what the page shows ----------

def test_the_crowd_is_hidden_until_you_answer(app, polymarket):
    ask()
    shown = forecast.view(EDITION, "ellery", OUT + timedelta(hours=1))["question"]
    assert "crowd" not in shown and shown["yours"] is None
    polymarket["market"] = market(10, "", 0.58, 5e5)
    forecast.answer(EDITION, "ellery", 70, OUT + timedelta(hours=2))
    shown = forecast.view(EDITION, "ellery", OUT + timedelta(hours=3))["question"]
    assert (shown["yours"], shown["crowd"]) == (70, 58)
    assert "crowd" not in forecast.view(EDITION, "someone else", OUT + timedelta(hours=3))["question"]


def test_last_weeks_result_and_a_late_one_carried_over(app):
    settled("2026-W43", "Will Lula win?", "yes", 0.22, closes=OUT - timedelta(days=14))     # shown last week
    late = settled("2026-W42", "Will it settle late?", "no", 0.5, closes=OUT - timedelta(days=21))
    late.settled = OUT - timedelta(days=3)                                  # after last week's edition came out
    settled("2026-W41", "Old news", "no", 0.5, closes=OUT - timedelta(days=28))
    db.session.add(Question(edition="2026-W44", state="closed", asked=OUT - timedelta(days=7), market="m",
                            slug="s", title="Will the Senate flip?", closes=OUT - timedelta(days=2), crowd=0.28))
    db.session.commit()
    shown = forecast.view(EDITION, "ellery", OUT)
    assert [r["title"] for r in shown["results"]] == ["Will the Senate flip?", "Will it settle late?"]
    assert (shown["results"][0]["state"], shown["results"][0]["crowd"]) == ("closed", 28)      # waiting; skipped
    w46 = forecast.view("2026-W46", "ellery", OUT + timedelta(days=8))
    assert [r["title"] for r in w46["results"]] == ["Will the Senate flip?"]                    # still waiting


def test_the_record(app):
    settled("2026-W40", "A", "yes", 0.3)
    settled("2026-W41", "B", "no", 0.7, subject="economics")
    settled("2026-W42", "C", "void", 0.5)
    for edition, chance, crowd in (("2026-W40", 80, 0.3), ("2026-W41", 20, 0.7), ("2026-W42", 50, 0.5)):
        db.session.add(Guess(edition=edition, user="ellery", chance=chance, crowd=crowd, at=OUT))
    db.session.commit()
    r = forecast.record("ellery")
    assert r["answered"] == 2                                                  # a void question isn't scored
    assert (r["yours"], r["crowd"]) == (pytest.approx(0.04), pytest.approx(0.49))
    assert {s["subject"]: s["answered"] for s in r["subjects"]} == {"elections": 1, "economics": 1}
    assert [(b["said"], b["happened"]) for b in r["bands"]] == [(0, 0), (1, 0), (0, 0), (0, 0), (1, 1)]


def test_an_edition_not_out_yet_has_no_forecast(app):
    assert forecast.view("2026-W46", "ellery", OUT) is None


def test_the_api(app, polymarket, monkeypatch):
    import pipeline
    ask()
    monkeypatch.setattr(pipeline, "clock", lambda: OUT + timedelta(hours=1))
    polymarket["market"] = market(10, "", 0.61, 5e5)
    client = app.test_client()
    r = client.post(f"/api/forecast/{EDITION}", json={"chance": 65})
    assert r.status_code == 200 and r.get_json()["question"]["crowd"] == 61
    assert client.post(f"/api/forecast/{EDITION}", json={"chance": 66}).status_code == 400
