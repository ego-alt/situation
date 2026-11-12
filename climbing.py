"""Climbing: the World Cup rounds worth watching around an issue.

From the sportclimbing/ifsc-calendar project's file of the season (github.com/sportclimbing/ifsc-calendar,
kept up from the federation's own pages): the semi-finals and finals in boulder and lead of the
World Cups and World Championships, each with its stream. No youth, para or speed climbing, and
no results: yesterday's rounds are listed to be watched, not spoiled.
"""
from datetime import UTC, datetime, timedelta

import requests

from papers import get
from pipeline import record_sources

CALENDAR = "https://github.com/sportclimbing/ifsc-calendar/releases/latest/download/IFSC-World-Cups-and-World-Championships.json"
KEY, NAME = "climbing", "the climbing calendar"  # its SourceStatus key, and its name on the page
LEAGUE = "World Cups and World Championships"   # the file has the paraclimbing series and the Games too
DISCIPLINES = {"boulder", "lead"}
KINDS = {"semi-final", "final"}                 # a qualification runs all day and isn't shown
BEFORE = timedelta(days=1)      # a round from the day before is there to catch up on
AHEAD = timedelta(days=7)


def rounds(calendar: dict, now: datetime) -> list[dict]:
    """The rounds to list at `now`, the soonest first: begun in the last BEFORE, or beginning in the next AHEAD."""
    out = []
    for e in calendar["events"]:
        for r in e["rounds"] if e["league_name"] == LEAGUE else []:
            starts, ends = (datetime.fromisoformat(r[at]).astimezone(UTC) for at in ("starts_at", "ends_at"))
            if r["kind"] in KINDS and DISCIPLINES.issuperset(r["disciplines"]) and now - BEFORE <= starts < now + AHEAD:
                out.append({"round": r["name"], "place": f'{e["location"]}, {e["country_name"]}',
                            "starts": starts.isoformat(), "ends": ends.isoformat(), "stream": r.get("stream_url")})
    return sorted(out, key=lambda r: r["starts"])


def ahead(now: datetime) -> list[dict]:
    """Read the calendar for the issue made up at `now`; nothing when it can't be read."""
    try:
        calendar = get(CALENDAR).json()
        found, report = rounds(calendar, now), (len(calendar["events"]), None)
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        found, report = [], (0, type(e).__name__)
    record_sources({KEY: report}, now)
    return found
