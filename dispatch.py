"""Dispatch: the daily issue to read, out each morning at 8 in Hong Kong.

An issue is made of sections, each a source as the news's outlets are (sources.py): a module
with its NAME, the SOURCES it reads (for the issue's one status line) and take(edition, day,
now, report), which reads them as the issue is made up and gives what the issue keeps (None,
and the issue goes without it that day). It notes how each read went in `report` and what it
ran (models.Shown, the papers their own `ran`), and nothing is kept until the issue is: an issue
is all of it or none. Every section reads its sources for the morning; only the poems, which
had to be put right first, are kept in the app (poems.py).

The sections, in the page's order: the papers the journals made more of, on the front page
(papers.py), a print (prints.py), the poem (poems.py), a bird (birds.py) and the dates coming up
(dates.py). The day's question (forecast.py) is asked as the issue comes out too, but kept on
its own: it's answered, and each reader is shown their own.

An issue is made up when it comes out (publish) and shown unchanged from then on. A periodical,
not an archive: a week of issues is kept.
"""
from datetime import UTC, date, datetime, time, timedelta

import birds
import dates
import papers
import poems
import prints
from models import Issue, SourceStatus, db

KEEP = timedelta(days=7)        # a week of issues, then they go
SECTIONS = (papers, prints, poems, birds, dates)


def out(edition: str) -> datetime:
    """When an issue comes out: on its day at midnight UTC (8am in Hong Kong)."""
    return datetime.combine(date.fromisoformat(edition), time.min, UTC)


def latest(now: datetime) -> str:
    """The issue out by `now`: an issue is named for its day (2026-11-12)."""
    return now.date().isoformat()


def publish(now: datetime) -> str | None:
    """Make up today's issue, if it isn't yet, and drop those older than KEEP; returns its
    edition when it was made. The front page waits for the journals to have answered since the
    issue came out; any other section that doesn't answer is only missed this once, and a day
    missed altogether has no issue."""
    edition = latest(now)
    if db.session.get(Issue, edition):
        return None
    if not papers.read_since(out(edition)):
        papers.fetch(now)
        if not papers.read_since(out(edition)):
            return None
    day = now.date()
    Issue.query.filter(Issue.edition < (day - KEEP).isoformat()).delete()
    report: dict = {}
    taken = {s.NAME: s.take(edition, day, now, report) for s in SECTIONS}
    db.session.add(Issue(edition=edition, out=out(edition), sections={k: v for k, v in taken.items() if v is not None}))
    db.session.commit()
    SourceStatus.record(report, now)
    return edition


def sources() -> dict:
    """How the places an issue is made from last answered: {"total", "failing": [names]}, and when one last did."""
    names = {key: name for s in SECTIONS for key, name in s.SOURCES.items()}
    rows = {r.key: r for r in SourceStatus.query.filter(SourceStatus.key.in_(names))}
    answered = max((r.last_ok for r in rows.values() if r.last_ok), default=None)
    failing = [name for key, name in names.items() if key in rows and rows[key].error]
    return {"sources": {"total": len(names), "failing": failing},
            "fetched": answered.isoformat() if answered else None}


def issue(which: str | None) -> dict:
    """One issue, as it came out (the newest by default), and its place among the others."""
    editions = [e for (e,) in db.session.query(Issue.edition).order_by(Issue.edition)]
    which = which if which in editions else (editions or [None])[-1]
    base = {"editions": editions, **sources()}
    if not which:
        return {**base, "edition": None, "sections": {}}
    i = db.session.get(Issue, which)
    at = editions.index(which)
    # Numbered by the day of the year.
    return {**base, "edition": {"id": which, "number": i.out.timetuple().tm_yday, "out": i.out.isoformat(),
                                "prev": editions[at - 1] if at else None,
                                "next": editions[at + 1] if at + 1 < len(editions) else None},
            "sections": i.sections}
