"""Dispatch: the weekly issue to read, one an ISO week, out the Monday after.

It carries the week's neuroscience (papers.py), the week's question (forecast.py, added by
the app, which knows who's reading) and the public holidays coming up. A periodical, not an
archive: the last KEEP_WEEKS issues are kept, from the first week the journals were read in full.
"""
from datetime import date, datetime, timedelta

import holidays

import papers
from journals import JOURNALS

KEEP_WEEKS = 4                  # the last four issues, then they go
HOLIDAYS = ("HK", "Hong Kong")  # whose public holidays it lists (the holidays library's code), and its name
AHEAD = timedelta(days=14)      # how far ahead from when an issue comes out


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


def kept_from(today: date) -> date:
    """The first day of the oldest issue kept."""
    return monday(today) - timedelta(weeks=KEEP_WEEKS)


# ---------- the issues ----------

def editions(today: date) -> list[str]:
    """The issues kept, oldest first: each week read in full with a paper in it (or a commentary
    on an earlier one), and this week's too, while it fills in."""
    covered = papers.covered_from()
    if not covered:
        return []
    first = max(covered if covered.weekday() == 0 else monday(covered) + timedelta(days=7), kept_from(today))
    return sorted({edition_id(d) for d in papers.published_days(first, monday(today) + timedelta(days=7))})


def prune(today: date) -> int:
    """Forget what's older than the issues kept."""
    return papers.prune(kept_from(today))


def holidays_ahead(start: date) -> list[dict]:
    """The public holidays (statutory and general) in the AHEAD from `start`, the day an issue comes out."""
    end = start + AHEAD
    found = holidays.country_holidays(HOLIDAYS[0], years={start.year, end.year}, language="en_HK",
                                      categories=("public", "optional"))
    return [{"date": d.isoformat(), "name": name} for d, name in sorted(found.items()) if start <= d < end]


def issue(which: str | None, today: date, fetched: datetime | None = None) -> dict:
    """One issue: its place among the others, its papers and the holidays ahead of it.
    Defaults to the newest finished week."""
    all_editions = editions(today)
    this_week = monday(today)
    finished = [e for e in all_editions if week_of(e) < this_week]
    which = which if which in all_editions else (finished or all_editions or [None])[-1]
    upcoming = {"out": (this_week + timedelta(days=7)).isoformat(),
                "so_far": len(papers.section(this_week))}
    base = {"editions": all_editions, "next_edition": upcoming, "fetched": fetched.isoformat() if fetched else None,
            "sources": papers.status(), "journals": [j.name for j in JOURNALS]}
    if not which:
        return {**base, "edition": None, "papers": [], "holidays": {"where": HOLIDAYS[1], "days": []}}
    start = week_of(which)
    at = all_editions.index(which)
    return {**base, "edition": {"id": which, "number": int(which[6:8]),
                                "start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat(),
                                "in_progress": start >= this_week,
                                "prev": all_editions[at - 1] if at else None,
                                "next": all_editions[at + 1] if at + 1 < len(all_editions) else None},
            "papers": papers.section(start),
            "holidays": {"where": HOLIDAYS[1], "days": holidays_ahead(start + timedelta(days=7))}}
