"""Dispatch: the daily issue to read, out each morning at 8 in Hong Kong.

Its front page is read in place: the papers the journals made more of (papers.py) and a piece
from each of features.py's places. Under it, a print from the Cleveland Museum of Art with what
the museum says of it, a poem from 唐詩三百首 or 宋詞三百首 to learn by heart, the day's question
(forecast.py, added by the app, which knows who's reading), the World Cup climbing to watch
(climbing.py) and the public holidays coming up. An issue is made up when it comes out
(publish), from what its sources say then, and shown unchanged from then on. A periodical, not
an archive: a week of issues is kept.
"""
import json
import pathlib
import random
from datetime import UTC, date, datetime, time, timedelta

import holidays

import climbing
import features
import papers
from journals import JOURNALS
from models import Issue, SourceStatus, db

KEEP = timedelta(days=7)        # a week of issues, then they go (longer than features.FRESH, so no piece comes twice)
HOLIDAYS = ("HK", "Hong Kong")  # whose public holidays it lists (the holidays library's code), and its name
AHEAD = timedelta(days=14)      # how far ahead from when an issue comes out
DATA = pathlib.Path(__file__).parent / "data"
# 唐詩三百首 and 宋詞三百首, in Hong Kong's characters (tools/add_poems.py).
POEMS = json.loads((DATA / "poems.json").read_text(encoding="utf-8"))
# Japanese and Chinese prints with the museum's text (tools/add_prints.py).
PRINTS = json.loads((DATA / "prints.json").read_text(encoding="utf-8"))


def out(edition: str) -> datetime:
    """When an issue comes out: on its day at midnight UTC (8am in Hong Kong)."""
    return datetime.combine(date.fromisoformat(edition), time.min, UTC)


def latest(now: datetime) -> str:
    """The issue out by `now`: an issue is named for its day (2026-11-12)."""
    return now.date().isoformat()


# ---------- publishing ----------

def holidays_ahead(start: date) -> list[dict]:
    """The public holidays (statutory and general) in the AHEAD from `start`, the day an issue comes out."""
    end = start + AHEAD
    found = holidays.country_holidays(HOLIDAYS[0], years={start.year, end.year}, language="en_HK",
                                      categories=("public", "optional"))
    return [{"date": d.isoformat(), "name": name} for d, name in sorted(found.items()) if start <= d < end]


def turn(items: list, name: str, day: date):
    """The one of `items` whose turn it is on `day`: each comes round once, in an order of its
    own, before any comes again."""
    round_, at = divmod(day.toordinal(), len(items))
    return random.Random(f"{name}:{round_}").sample(items, len(items))[at]


def front(stories: list[dict]) -> list[dict]:
    """The front page's order: a cover story leads, else the longest read; the rest as they came."""
    lead = max(stories, key=lambda s: (s["cover"], len(s["abstract"])), default=None)
    return [lead, *(s for s in stories if s is not lead)] if lead else []


def publish(now: datetime) -> str | None:
    """Make up today's issue, if it isn't yet, and drop those older than KEEP; returns its
    edition when it was made. It waits for the journals to have answered since it came out (a
    place that doesn't answer is only late: its pieces wait for tomorrow), and a day missed
    altogether has no issue."""
    edition = latest(now)
    if db.session.get(Issue, edition):
        return None
    if not papers.read_since(out(edition)):
        papers.fetch(now)
        if not papers.read_since(out(edition)):
            return None
    day = now.date()
    Issue.query.filter(Issue.edition < (day - KEEP).isoformat()).delete()
    carried = {s["id"] for i in Issue.query for s in i.stories}
    stories = papers.take(edition, day) + features.take(now, carried)
    db.session.add(Issue(edition=edition, out=out(edition), stories=front(stories), print=turn(PRINTS, "print", day),
                         poem=turn(POEMS, "poem", day), holidays=holidays_ahead(day), climbing=climbing.ahead(now)))
    db.session.commit()
    return edition


# ---------- reading ----------

def sources() -> dict:
    """How the places an issue is made from last answered: {"total", "failing": [names]}, and when one last did."""
    names = {f"papers:{j.key}": j.name for j in JOURNALS} | {f"features:{f.key}": f.name for f in features.FEATURES}
    names[climbing.KEY] = climbing.NAME
    rows = {r.key: r for r in SourceStatus.query.filter(SourceStatus.key.in_(names))}
    answered = max((r.last_ok for r in rows.values() if r.last_ok), default=None)
    failing = [name for key, name in names.items() if key in rows and rows[key].error]
    return {"sources": {"total": len(names), "failing": failing},
            "fetched": answered.isoformat() if answered else None}


def issue(which: str | None) -> dict:
    """One issue, as it came out (the newest by default), and its place among the others."""
    editions = [e for (e,) in db.session.query(Issue.edition).order_by(Issue.edition)]
    which = which if which in editions else (editions or [None])[-1]
    base = {"editions": editions, **sources(), "holidays": {"where": HOLIDAYS[1], "days": []}}
    if not which:
        return {**base, "edition": None, "stories": [], "print": None, "poem": None, "climbing": []}
    i = db.session.get(Issue, which)
    at = editions.index(which)
    # Numbered by the day of the year.
    return {**base, "edition": {"id": which, "number": i.out.timetuple().tm_yday, "out": i.out.isoformat(),
                                "prev": editions[at - 1] if at else None,
                                "next": editions[at + 1] if at + 1 < len(editions) else None},
            "stories": i.stories, "print": i.print, "poem": i.poem, "climbing": i.climbing,
            "holidays": {"where": HOLIDAYS[1], "days": i.holidays}}
