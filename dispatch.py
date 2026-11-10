"""Dispatch: the weekly issue to read, one an ISO week, out the Wednesday after.

It carries the week's neuroscience the journals made more of (papers.py) and Quanta Magazine's
articles from the week before it came out (quanta.py), a poem from 唐詩三百首 or 宋詞三百首 to
learn by heart, the week's question (forecast.py, added by the app, which knows who's reading)
and the public holidays coming up. An issue comes
out on the Wednesday after its week, once the journals have mostly been listed, and is
published then (publish): what it carries is kept as it stood and shown unchanged from then on.
A paper listed too late for its own week's issue is in the next one, marked with its date. A
periodical, not an archive: the last KEEP_WEEKS issues are kept.
"""
import json
import pathlib
import random
from datetime import UTC, date, datetime, timedelta

import holidays
import requests

import papers
import quanta
from models import Issue, db

KEEP_WEEKS = 4                  # the last four issues, then they go
OUT = timedelta(days=9)         # from the Monday a week starts until its issue comes out: Wednesday after
HOLIDAYS = ("HK", "Hong Kong")  # whose public holidays it lists (the holidays library's code), and its name
AHEAD = timedelta(days=14)      # how far ahead from when an issue comes out
# 唐詩三百首 and 宋詞三百首, in Hong Kong's characters (tools/add_poems.py).
POEMS = json.loads((pathlib.Path(__file__).parent / "data" / "poems.json").read_text(encoding="utf-8"))


# ---------- weeks ----------

def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def edition_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_of(edition: str) -> date | None:
    try:
        return date.fromisocalendar(int(edition[:4]), int(edition[6:8]), 1)
    except (ValueError, IndexError):
        return None


def previous(edition: str) -> str:
    return edition_id(week_of(edition) - timedelta(days=7))


def out(edition: str) -> datetime:
    """When an issue comes out: the Wednesday after its week, at midnight UTC (8am in Hong Kong)."""
    return datetime.combine(week_of(edition) + OUT, datetime.min.time(), UTC)


def latest(now: datetime) -> str:
    """The newest issue out by `now`."""
    edition = edition_id(monday(now.date()) - timedelta(days=7))
    return edition if out(edition) <= now else previous(edition)


def kept_from(today: date) -> date:
    """The first day of the oldest issue kept."""
    return monday(today) - timedelta(weeks=KEEP_WEEKS)


# ---------- publishing ----------

def holidays_ahead(start: date) -> list[dict]:
    """The public holidays (statutory and general) in the AHEAD from `start`, the day an issue comes out."""
    end = start + AHEAD
    found = holidays.country_holidays(HOLIDAYS[0], years={start.year, end.year}, language="en_HK",
                                      categories=("public", "optional"))
    return [{"date": d.isoformat(), "name": name} for d, name in sorted(found.items()) if start <= d < end]


def quanta_before(end: datetime) -> list[dict]:
    """Quanta's articles in the week before an issue comes out; none when it doesn't answer (the
    issue still comes out, without them)."""
    try:
        return quanta.articles(quanta.read(), end)
    except requests.RequestException:
        return []


def draw_poem(shown: list[dict], rng: random.Random) -> dict:
    """A poem at random, never one in an issue still kept."""
    seen = {(p["author"], p["lines"][0]) for p in shown}
    return rng.choice([p for p in POEMS if (p["author"], p["lines"][0]) not in seen])


def publish(now: datetime) -> list[str]:
    """Publish every issue that's out by `now` and not yet published, once the journals have
    been read since it came out, from the first week they were read in full (and no further
    back than the issues kept). Returns the editions published."""
    covered, fetched = papers.covered_from(), papers.last_fetched()
    if not covered or not fetched:
        return []
    first = max(covered if covered.weekday() == 0 else monday(covered) + timedelta(days=7), kept_from(now.date()))
    done = []
    week = first
    while out(edition_id(week)) <= min(now, fetched):
        edition = edition_id(week)
        if not db.session.get(Issue, edition):
            before = db.session.get(Issue, previous(edition))
            shown = {p["doi"] for p in before.papers} if before else None
            # Listed too late for last week's issue: in this one. (The first issue has none to catch up on.)
            late = papers.research_between(week - timedelta(days=7), week) - shown if shown is not None else set()
            db.session.add(Issue(edition=edition, out=out(edition), papers=papers.section(week, late),
                                 quanta=quanta_before(out(edition)), holidays=holidays_ahead(out(edition).date()),
                                 poem=draw_poem([i.poem for i in Issue.query if i.poem], random.Random())))
            db.session.commit()
            done.append(edition)
        week += timedelta(days=7)
    return done


def prune(today: date) -> int:
    """Forget the papers and issues older than the issues kept."""
    cutoff = kept_from(today)
    Issue.query.filter(Issue.edition < edition_id(cutoff)).delete()
    return papers.prune(cutoff)


# ---------- reading ----------


def issue(which: str | None, now: datetime) -> dict:
    """One published issue, as it came out (the newest by default), and its place among the others."""
    editions = [e for (e,) in db.session.query(Issue.edition).order_by(Issue.edition)]
    which = which if which in editions else (editions or [None])[-1]
    base = {"editions": editions, "next_out": out(edition_id(week_of(latest(now)) + timedelta(days=7))).isoformat(),
            "sources": papers.status(), "fetched": papers.last_fetched().isoformat() if papers.last_fetched() else None}
    if not which:
        return {**base, "edition": None, "stories": [], "poem": None, "holidays": {"where": HOLIDAYS[1], "days": []}}
    i = db.session.get(Issue, which)
    start, at = week_of(which), editions.index(which)
    return {**base, "edition": {"id": which, "number": int(which[6:8]), "out": i.out.isoformat(),
                                "start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat(),
                                "prev": editions[at - 1] if at else None,
                                "next": editions[at + 1] if at + 1 < len(editions) else None},
            "stories": i.papers + (i.quanta or []), "poem": i.poem,
            "holidays": {"where": HOLIDAYS[1], "days": i.holidays}}
