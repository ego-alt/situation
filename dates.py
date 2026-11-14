"""Dates: the days worth knowing are coming, each said four times: two weeks ahead, a week
ahead, the day before, and while it's on. Not every morning in between: a line that's always
there stops being read.

Three calendars. Hong Kong's public holidays (the holidays library), a break of them as one.
The World Cup climbing, from the sportclimbing/ifsc-calendar project's file of the season
(github.com/sportclimbing/ifsc-calendar, kept up from the federation's own pages): the World
Cups and World Championships with a semi-final or final in boulder or lead, each round with its
stream; no youth, para or speed climbing. And the UFC's cards, from ESPN's schedule, with the
main card's bouts. Days are Hong Kong's, as an issue's is. No results: a round or a card over
by the time the issue is read is there to be watched, not spoiled.
"""
from datetime import UTC, date, datetime, timedelta, timezone

import holidays
import requests

from models import SourceStatus
from papers import get

BEATS = (14, 7, 1)              # the days ahead a date is said on, before the days it's on
HONG_KONG = timezone(timedelta(hours=8))    # no summer time there
HOLIDAYS = "HK"                 # the holidays library's code
CLIMBING = "https://github.com/sportclimbing/ifsc-calendar/releases/latest/download/IFSC-World-Cups-and-World-Championships.json"
LEAGUE = "World Cups and World Championships"   # the file has the paraclimbing series and the Games too
DISCIPLINES = {"boulder", "lead"}
ROUNDS = {"semi-final", "final"}                # a qualification runs all day and isn't shown
UFC = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
SOURCES = {"dates:climbing": "the climbing calendar", "dates:ufc": "the UFC’s schedule"}   # SourceStatus key → name


def entry(kind: str, title: str, day: date, until: date | None = None, **more) -> dict:
    """A date: what it is, the Hong Kong days it runs from and to, and what's known of it. Its
    parts are what there is to watch, each {"name", "starts", "ends", "link"}."""
    return {"kind": kind, "title": title, "day": day.isoformat(), "until": (until or day).isoformat(),
            "place": None, "note": None, "link": None, "parts": [], **more}


def _part(name: str, starts: str, ends: str | None = None, link: str | None = None) -> dict:
    return {"name": name, "starts": _utc(starts).isoformat(), "ends": ends and _utc(ends).isoformat(), "link": link}


def _utc(moment: str) -> datetime:
    return datetime.fromisoformat(moment).astimezone(UTC)


def _day(moment: str) -> date:
    """Hong Kong's day at a moment."""
    return datetime.fromisoformat(moment).astimezone(HONG_KONG).date()


# ---------- the three calendars ----------

def breaks(first: date, last: date) -> list[dict]:
    """Hong Kong's public holidays (statutory and general) between two days. Holidays a day apart,
    or either side of a Sunday, are one break: named for its first, the others in its note."""
    found = holidays.country_holidays(HOLIDAYS, years={first.year, last.year}, language="en_HK",
                                      categories=("public", "optional"))
    runs: list[list[tuple[date, str]]] = []
    for d, names in sorted(found.items()):
        gap = d - runs[-1][-1][0] if runs else None
        if gap == timedelta(days=1) or (gap == timedelta(days=2) and d.weekday() == 0):
            runs[-1].append((d, names))
        else:
            runs.append([(d, names)])
    out = []
    for run in runs:
        if run[-1][0] < first or run[0][0] > last:
            continue
        # A day that is two holidays has both names; one moved off a Sunday is named again, "(observed)".
        names = list(dict.fromkeys(n.removesuffix(" (observed)") for _, both in run for n in both.split("; ")))
        out.append(entry("Holiday", names[0], run[0][0], run[-1][0], note="; ".join(names[1:]) or None))
    return out


def climbing(calendar: dict) -> list[dict]:
    """The season's competitions with rounds to watch, those rounds their parts."""
    out = []
    for e in calendar["events"]:
        rounds = [r for r in e["rounds"] if r["kind"] in ROUNDS and DISCIPLINES.issuperset(r["disciplines"])]
        if e["league_name"] != LEAGUE or not rounds:
            continue
        rounds.sort(key=lambda r: _utc(r["starts_at"]))
        held = sorted({d for r in rounds for d in r["disciplines"]})
        out.append(entry("Climbing", e["name"], _day(rounds[0]["starts_at"]), _day(rounds[-1]["starts_at"]),
                         place=f'{e["location"]}, {e["country_name"]}', note=" and ".join(held).capitalize(),
                         link=e.get("site_url"),
                         parts=[_part(r["name"], r["starts_at"], r["ends_at"], r.get("stream_url")) for r in rounds]))
    return out


def cards(schedule: dict) -> list[dict]:
    """The UFC's cards, each on the day its main card starts: the bouts that start last, the
    headline (listed last) first in its note."""
    out = []
    for e in schedule["events"]:
        if not e["name"].startswith("UFC") or "CANCELED" in e["status"]["type"]["name"]:   # not the Contender Series
            continue
        bouts = e.get("competitions") or []
        first, main = (bouts[0]["date"], bouts[-1]["date"]) if bouts else (e["date"], e["date"])
        names = [" vs. ".join(c["athlete"]["displayName"] for c in sorted(b["competitors"], key=lambda c: c["order"]))
                 for b in reversed(bouts) if b["date"] == main]
        address = (e.get("venues") or [{}])[0].get("address", {})
        parts = [_part("Prelims", first)] if first != main else []
        out.append(entry("UFC", e["name"], _day(main), note="; ".join(names) or None,
                         place=", ".join(filter(None, (address.get("city"), address.get("country")))) or None,
                         link=next((link["href"] for link in e.get("links", [])), None),
                         parts=[*parts, _part("Main card", main)]))
    return out


# ---------- for the issue (dispatch.py) ----------

def due(d: dict, day: date) -> bool:
    """Whether a date is said on `day`: a beat before it starts, or while it's on."""
    starts, ends = date.fromisoformat(d["day"]), date.fromisoformat(d["until"])
    return (starts - day).days in BEATS or starts <= day <= ends


def ahead(day: date, now: datetime) -> list[dict]:
    """The dates to say in the issue of `day`, made up at `now`, the soonest first. A calendar
    that can't be read is missed this once."""
    last = day + timedelta(days=max(BEATS))
    span = f"{day - timedelta(days=1):%Y%m%d}-{last + timedelta(days=1):%Y%m%d}"   # ESPN's days are America's
    reads = {"dates:climbing": lambda: climbing(get(CLIMBING).json()),
             "dates:ufc": lambda: cards(get(UFC, dates=span).json())}
    found, report = breaks(day, last), {}
    for key, read in reads.items():
        try:
            got = read()
            found, report[key] = found + got, (len(got), None)
        except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as e:
            report[key] = (0, type(e).__name__)
    SourceStatus.record(report, now)
    return sorted((d for d in found if due(d, day)), key=lambda d: (d["day"], d["kind"], d["title"]))
