"""Signals: what disaster-monitoring services see, often before the news does.

GDACS (the UN and EU's Global Disaster Alert and Coordination System) grades
each earthquake, tropical cyclone, eruption and flood by its likely human
impact: only orange and red alerts count here. Droughts are left out: they
cover half a continent for months, which is a situation, not a signal.
USGS reports earthquakes within minutes: one counts at magnitude 6.5 or more,
or when its PAGER estimate expects damage (yellow or worse), or a tsunami.
The same earthquake from both is one signal.

A signal is shown while it's happening and for SHOWN after (an earthquake
happens at once; a cyclone or flood lasts until its end date)."""
import math
import re
from datetime import UTC, datetime, timedelta

import requests

from geo import places
from models import Signal, SourceStatus, db
from sources import USER_AGENT

GDACS = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
USGS = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"
KINDS = {"EQ": "earthquake", "TC": "cyclone", "VO": "eruption", "FL": "flood"}
MIN_MAGNITUDE = 6.5
PAGER = {"yellow", "orange", "red"}
SHOWN = timedelta(hours=48)
SAME_QUAKE = (timedelta(minutes=30), 150)      # within half an hour and 150 km: one earthquake
TIMEOUT = 20


def _get(url, **params):
    resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _utc(text: str) -> datetime:
    """GDACS's times, which are UTC without saying so."""
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t.astimezone(UTC) if t.tzinfo else t.replace(tzinfo=UTC)


def _iso2(iso3: str | None) -> str | None:
    from geo import _table
    return next((iso for iso, c in _table()[0].items() if c.get("iso3") == iso3), None) if iso3 else None


def _title(kind: str, name: str, magnitude: float | None) -> str:
    if kind == "EQ":
        return f"M{magnitude:.1f} earthquake" if magnitude else "Earthquake"
    if kind == "TC":
        return f"Cyclone {name.split('-')[0].title()}" if name else "Tropical cyclone"
    if kind == "VO":
        return f"{name} eruption" if name else "Eruption"
    return "Flooding"


def _places(names: str | None) -> str | None:
    """"Philippines, Guam, Japan" as "Philippines and 2 more"."""
    named = [n.strip() for n in (names or "").split(",") if n.strip()]
    return None if not named else named[0] if len(named) == 1 else f"{named[0]} and {len(named) - 1} more"


def gdacs(record: dict) -> dict | None:
    """One GDACS event (a GeoJSON feature) as a signal, or None when it doesn't count."""
    p = record.get("properties", {})
    kind, level = p.get("eventtype"), (p.get("alertlevel") or "").lower()
    if kind not in KINDS or level not in ("orange", "red") or not record.get("geometry"):
        return None
    lon, lat = record["geometry"]["coordinates"][:2]
    magnitude = (p.get("severitydata") or {}).get("severity") if kind == "EQ" else None
    name = p.get("eventname") or ""
    if kind == "VO":
        name = re.sub(r"^Eruption\s+", "", p.get("name") or "").strip()
    affected = [c.get("iso2") for c in p.get("affectedcountries") or [] if c.get("iso2")]
    iso = affected[0] if affected else _iso2(p.get("iso3"))
    url = p.get("url") or {}
    return {"id": f"gdacs:{kind}:{p.get('eventid')}", "source": "GDACS", "kind": kind, "level": level,
            "title": _title(kind, name, magnitude), "place": _places(p.get("country")), "iso": iso,
            "lat": lat, "lon": lon, "magnitude": magnitude, "started": _utc(p["fromdate"]),
            "ended": _utc(p.get("todate") or p["fromdate"]),
            "url": url.get("report") if isinstance(url, dict) else url}


def usgs(record: dict) -> dict | None:
    """One USGS earthquake as a signal, or None when it's below the bar."""
    p = record.get("properties", {})
    magnitude, alert = p.get("mag") or 0, (p.get("alert") or "").lower()
    if magnitude < MIN_MAGNITUDE and alert not in PAGER and not p.get("tsunami"):
        return None
    lon, lat = record["geometry"]["coordinates"][:2]
    at = datetime.fromtimestamp(p["time"] / 1000, UTC)
    named = places(p.get("place") or "")
    return {"id": f"usgs:{record.get('id')}", "source": "USGS", "kind": "EQ",
            "level": alert if alert in ("orange", "red") else "orange", "title": _title("EQ", "", magnitude),
            "place": p.get("place"), "iso": named.most_common(1)[0][0] if named else None,
            "lat": lat, "lon": lon, "magnitude": magnitude, "started": at, "ended": at, "url": p.get("url")}


def _km(a: dict, b: dict) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def merge(found: list[dict]) -> list[dict]:
    """One signal per earthquake: GDACS's grading, USGS's magnitude and its earlier time."""
    gd = [s for s in found if s["source"] == "GDACS"]
    out = list(gd)
    for s in (s for s in found if s["source"] == "USGS"):
        twin = next((g for g in gd if g["kind"] == "EQ" and abs(g["started"] - s["started"]) <= SAME_QUAKE[0]
                     and _km(g, s) <= SAME_QUAKE[1]), None)
        if twin:
            twin["magnitude"] = s["magnitude"] or twin["magnitude"]
            twin["title"] = _title("EQ", "", twin["magnitude"])
            twin["started"] = min(twin["started"], s["started"])
            twin["place"] = s["place"] or twin["place"]
        else:
            out.append(s)
    return out


def fetch(now: datetime | None = None, get=_get) -> int:
    """Read both services and keep what counts; returns how many signals are current."""
    now = now or datetime.now(UTC)
    week = {"fromdate": (now - timedelta(days=7)).date().isoformat(),
            "todate": (now + timedelta(days=1)).date().isoformat(), "alertlevel": "Orange;Red"}
    reads = {"gdacs": lambda: [gdacs(f) for f in get(GDACS, **week).get("features", [])],
             "usgs": lambda: [usgs(f) for f in get(USGS).get("features", [])]}
    found = []
    for key, read in reads.items():
        status = db.session.get(SourceStatus, f"signals:{key}") or SourceStatus(key=f"signals:{key}")
        db.session.add(status)
        status.last_try = now
        try:
            got = [s for s in read() if s]
            found += got
            status.last_ok, status.items, status.error = now, len(got), None
        except (requests.RequestException, ValueError, KeyError) as e:
            status.error = type(e).__name__
    for s in merge(found):
        row = db.session.get(Signal, s["id"])
        if row is None:
            row = Signal(id=s["id"], seen=now)
            db.session.add(row)
        for k, v in s.items():
            setattr(row, k, v)
    Signal.query.filter(Signal.ended < now - timedelta(days=30)).delete()
    db.session.commit()
    return len(current(now))


def current(now: datetime) -> list[Signal]:
    """The signals to show: happening, or ended within SHOWN; red first, then newest."""
    rows = Signal.query.filter(Signal.ended >= now - SHOWN, Signal.started <= now).all()
    return sorted(rows, key=lambda s: (s.level != "red", -s.started.timestamp()))


# What a story calls each kind, to tie a signal to the news about it.
WORDS = {"EQ": r"earthquake|quake|tremor|tsunami", "TC": r"cyclone|typhoon|hurricane|storm",
         "VO": r"eruption|volcano|erupt", "FL": r"flood"}


def about(signal: Signal, text: str) -> bool:
    """A story is about this signal: it names its kind (and, for a named storm or volcano, maybe the name)."""
    return bool(re.search(WORDS[signal.kind], text, re.I))

