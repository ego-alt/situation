"""The long-running situations: the wars Wikipedia's "In the news" lists as "Ongoing".

Each has the countries its name gives ("Russo-Ukrainian war": Russia and Ukraine). What's
about it is told in the words of conflict and names one of them; today's stories about it
are its latest developments, and every city and region its week of reporting named is its
track on the map. (Running stories the news itself reveals, an election night, are
situations.py.)"""
import re
from datetime import datetime, timedelta

import situations
from geo import places
from models import Article, Story, db
from pipeline import SHOWN
from score import is_soft

TRACK = timedelta(days=7)       # how far back a situation's reporting is read
TRACK_SPOTS = 40                # at most this many places on its track
QUOTED = 4                      # at most this many of today's stories listed under it

# The words of a war or a crisis being fought: a situation's news uses them.
CONFLICT = re.compile(
    r"\b(wars?|strikes?|struck|attacks?|attacked|drones?|missiles?|rockets?|shell(?:s|ing|ed)|bomb\w*|air ?strikes?"
    r"|troops|soldiers|army|military|forces|front ?lines?|offensives?|counteroffensive|ceasefire|truce"
    r"|peace (?:talks|deal|plan|process)|killed|dead|deaths|casualties|wounded|invasion|invaded|occupied"
    r"|occupation|captured|seized|hostages?|prisoners?|militants?|rebels?|insurgents?|fighting|clashes"
    r"|battles?|siege|blockade|nuclear|navy|naval|warships?|shipping)\b", re.I)


def _text(title: str, summary: str | None) -> str:
    return f"{title}. {summary or ''}"


def _fought(text: str) -> set[str]:
    """The countries a piece of text names, if it uses the words of conflict (else none):
    it's about a situation when one of them is the situation's."""
    return set(places(text)) if CONFLICT.search(text) else set()


def about_situation(story: Story, isos: set[str]) -> bool:
    """A story is about a situation (the war, not just a place in it): its headline names one of
    the situation's countries, and it or most of the story's articles do so in the words of
    conflict, wherever the story is placed: a Russian strike reported from Germany is the war;
    a ban on Russia's ice hockey team, placed in Russia, mostly isn't told in the war's words,
    and a vice president's frustration at the midterms whose articles blame the Iran war is
    about the midterms. Sport and entertainment (score.is_soft, as for the main list) never are."""
    articles = [{"title": a.title, "summary": a.summary} for a in story.articles]
    if not articles or is_soft(articles) or not set(places(story.headline)) & isos:
        return False
    if _fought(story.headline) & isos:
        return True
    return sum(bool(_fought(_text(a["title"], a["summary"])) & isos) for a in articles) * 2 > len(articles)


def _near(story: Story, core) -> bool:
    """A story is near a situation's core in meaning (embed.py); anything is, without the model."""
    if core is None:
        return True
    v = situations.meaning([{"title": a.title, "summary": a.summary} for a in story.articles])
    return v is not None and float(v @ core) >= situations.NEAR


def _track(reports: list[tuple], isos: set[str]) -> list[dict]:
    """Where a situation's reporting has been: each city or region its articles named in its
    countries, with how many articles named it and when last; the most named first."""
    found: dict[tuple, dict] = {}
    for _, when, spots in reports:
        for s in spots or []:
            if s["iso"] in isos:
                spot = found.setdefault((s["name"], s["iso"]), {**s, "n": 0, "last": when})
                spot["n"] += 1
                spot["last"] = max(spot["last"], when)
    return sorted(found.values(), key=lambda s: -s["n"])[:TRACK_SPOTS]


def follow(topics: list[str], now: datetime) -> list[dict]:
    """Each "Ongoing" topic as {"topic", "isos", "stories", "latest", "singles", "track"}:
    today's stories about it that two or more outlets carry (the most carried first, QUOTED at
    most), the first of them its latest development, how many more only one outlet has (as
    often an essay as news: counted, not quoted), and its week's track."""
    if not topics:
        return []
    # The week's articles told in the words of conflict, read once for every topic.
    week = []
    rows = db.session.query(Article.title, Article.summary, Article.published, Article.spots)
    for title, summary, published, spots in rows.filter(Article.published >= now - TRACK):
        named = _fought(_text(title, summary))
        if named:
            week.append((named, {"title": title, "summary": summary}, published, spots))
    today = Story.query.filter(Story.closed.is_(False), Story.newest >= now - SHOWN).order_by(Story.id).all()
    out = []
    for topic in topics:
        isos = set(places(topic.replace("Russo-Ukrainian", "Russian Ukrainian")))
        reports = [report for named, *report in week if named & isos]
        # A story about it is also near, in meaning, to the week's coverage of it, as a running story's
        # events are to its core (a hijacked Iranian fishing boat off Somalia names Iran, but isn't the Iran war).
        core = situations.meaning([article for article, _, _ in reports])
        here = [s for s in today if about_situation(s, isos) and _near(s, core)]
        shared = sorted((s for s in here if s.outlets >= 2), key=lambda s: (-s.outlets, -s.score))
        out.append({"topic": topic, "isos": sorted(isos), "stories": shared[:QUOTED],
                    "latest": shared[0] if shared else None, "singles": len(here) - len(shared),
                    "track": _track(reports, isos)})
    return out
