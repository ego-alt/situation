"""Signals (signals.py): GDACS's orange and red alerts and strong USGS earthquakes become
signals, one per earthquake, shown while they happen and for two days after."""
from datetime import UTC, datetime, timedelta

import signals
from models import Article, Signal, Story, db
from page import _signals

NOW = datetime(2026, 11, 4, 12, tzinfo=UTC)


def gdacs(kind, level, eventid, when, lon, lat, until=None, name="", country="India", iso2="IN", magnitude=None):
    return {"geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"eventtype": kind, "alertlevel": level, "eventid": eventid, "eventname": name,
                           "name": f"Eruption  {name}" if kind == "VO" else name, "country": country, "iso3": None,
                           "fromdate": when, "todate": until or when, "url": {"report": f"https://gdacs.example/{eventid}"},
                           "severitydata": {"severity": magnitude} if magnitude else {},
                           "affectedcountries": [{"iso2": iso2}]}}


def usgs(code, mag, when, lon, lat, place, alert=None, tsunami=0):
    return {"id": code, "geometry": {"type": "Point", "coordinates": [lon, lat, 10]},
            "properties": {"mag": mag, "place": place, "time": int(when.timestamp() * 1000), "alert": alert,
                           "tsunami": tsunami, "url": f"https://usgs.example/{code}"}}


QUAKE = NOW - timedelta(hours=3)
GDACS = {"features": [
    gdacs("TC", "Red", 1, "2026-11-01T00:00:00", 80.2, 15.8, until="2026-11-03T06:00:00", name="MURASU-26"),
    gdacs("EQ", "Orange", 2, QUAKE.strftime("%Y-%m-%dT%H:%M:%S"), 93.78, 32.38,
          country="China", iso2="CN", magnitude=6.9),
    gdacs("DR", "Orange", 3, "2026-05-01T00:00:00", 12.9, 48.1, until="2026-11-02T00:00:00"),   # drought: no signal
    gdacs("FL", "Green", 4, "2026-11-03T00:00:00", 78.6, 11.3),                                  # green: below the bar
    gdacs("VO", "Orange", 5, "2026-10-08T10:29:00", 121.0, 14.0, name="Taal", country="Philippines", iso2="PH"),
]}
USGS = {"features": [
    usgs("us1", 7.0, QUAKE + timedelta(minutes=2), 93.70, 32.30, "120 km W of Yushu, China"),   # GDACS's quake again
    usgs("us2", 6.0, NOW - timedelta(hours=1), 167.4, -15.2, "43 km ESE of Port-Olry, Vanuatu"),   # small, at sea
    usgs("us3", 6.1, NOW - timedelta(hours=2), 26.1, 38.4, "10 km N of Izmir, Turkey", alert="yellow"),  # damage likely
]}


def fake_get(url, **params):
    return GDACS if url == signals.GDACS else USGS


def test_only_what_clears_the_bar_is_a_signal():
    assert signals.gdacs(GDACS["features"][2]) is None and signals.gdacs(GDACS["features"][3]) is None
    assert signals.usgs(USGS["features"][1]) is None
    cyclone = signals.gdacs(GDACS["features"][0])
    assert (cyclone["title"], cyclone["level"], cyclone["iso"]) == ("Cyclone Murasu", "red", "IN")
    assert signals.gdacs(GDACS["features"][4])["title"] == "Taal eruption"
    assert signals.usgs(USGS["features"][2])["iso"] == "TR"


def test_one_signal_per_earthquake(app):
    signals.fetch(NOW, get=fake_get)
    quakes = Signal.query.filter_by(kind="EQ").all()
    assert len(quakes) == 2                                   # China (from both) and Turkey
    china = next(q for q in quakes if q.iso == "CN")
    assert china.source == "GDACS" and china.magnitude == 7.0 and china.title == "M7.0 earthquake"
    assert china.place == "120 km W of Yushu, China"


def test_signals_show_while_they_happen_and_two_days_after(app):
    signals.fetch(NOW, get=fake_get)
    now = NOW
    shown = {s.title for s in signals.current(now)}
    assert shown == {"Cyclone Murasu", "M7.0 earthquake", "M6.1 earthquake"}    # Taal erupted in October
    assert signals.current(now)[0].level == "red"                                # red first
    assert "Cyclone Murasu" not in {s.title for s in signals.current(now + timedelta(days=2))}


def test_a_story_about_the_event_is_tied_to_its_signal(app):
    signals.fetch(NOW, get=fake_get)
    story = Story(headline="Strong earthquake shakes western China", tier="headline", iso="CN", outlets=5,
                  first_seen=QUAKE)
    story.articles.append(Article(url="https://ap.example/q", source="ap", outlet="ap", title="Quake hits Qinghai",
                                  published=(QUAKE + timedelta(minutes=40))))
    db.session.add(story)
    db.session.commit()
    china = next(s for s in _signals(NOW, [story]) if s["iso"] == "CN")
    assert china["story_id"] == story.id and china["lead"] == 40   # seen 40 minutes before the first report
