"""Stage 3 of the pipeline: running stories (docs/design.md).

A running story is many events that are part of one thing: an election night's
race calls, a hurricane's landfall and its damage, a summit's meetings. Each
event alone may be small, and each is worded differently ("Ossoff wins Georgia
Senate race", "Ballot shortages in Dearborn"), so no event carries the breadth
the whole story has. Wikipedia's "Ongoing" list covers the long-running ones
(wars); this finds the rest from the news itself.

How: the coverage of a running story suddenly uses words it didn't use the
fortnight before ("midterm" in 138 articles from 15 outlets in a day, five
times its usual rate); a name that's always in the news ("Trump") doesn't
count. Such bursting words that mostly appear together are one story (a
topic). An event belongs to it when most of its articles use three of its
words, or two with one of its own (mostly used alongside its main word), and
it's near the story in meaning. A story is kept only when several of its
events are carried by more than one outlet and no one event holds most of its
articles (one big event, a train crash, is already a story), and it stays in one
country (two elections in one week are two stories). It's named by the phrase
most of its events use, as their headlines write it; without one, the words were
only shared by the day's stories ("president"), and there's no running story.
"""
import re
from collections import Counter, defaultdict
from datetime import timedelta
from functools import lru_cache

from geo import ALIASES, DEMONYMS, country, place_names, places
from sources import outlet
from text import STOPWORDS, names, tokens, where

DAY = timedelta(hours=24)        # the burst: the last day...
BASELINE_DAYS = 14               # ...against the fortnight before
MIN_BASELINE_DAYS = 7            # with less history than this, nothing is found
MIN_ARTICLES = 12                # a bursting word: in this many of the day's articles,
MIN_OUTLETS = 6                  # from this many outlets,
MIN_LIFT = 3.0                   # at least this many times its usual daily rate
COMMON_NAME = 15                 # a name already in this many articles a day ("Trump") defines no story
TOGETHER = 0.4                   # two words are one story when this share of the rarer one's articles use both
OWN = 0.5                        # a story's own word: at least this share of its articles also use the main word
JOIN = 0.5                       # an event joins when more than this share of its articles match
MIN_EVENTS = 3                   # a story has at least this many events,
MIN_SHARED = 2                   # this many of them carried by two or more outlets,
DOMINANT = 0.7                   # and no one event holds more than this share of its articles
NEAR = 0.45                      # an event's likeness in meaning to the story's core (embed.py)
# Words a story's name doesn't start or end on.
_NOT_NAME = STOPWORDS | {"since", "ahead", "during", "until", "against", "despite", "via", "per"}
# Words that burst with the calendar, not the news.
_CALENDAR = set("monday tuesday wednesday thursday friday saturday sunday tonight today yesterday "
                "weekend morning evening night week month".split())


def outlets(group: list[dict]) -> int:
    return len({outlet(a) for a in group})


def words(article: dict) -> set[str]:
    """The terms an article uses: its title and the start of its summary."""
    return set(tokens(article["title"])) | set(tokens(" ".join((article.get("summary") or "").split()[:40])))


def baseline(articles: list[dict]) -> tuple[Counter, int]:
    """How many articles used each term, and over how many days."""
    counts = Counter(t for a in articles for t in words(a))
    days = len({a["published"].date() for a in articles})
    return counts, days


def bursting(day: list[dict], usual: Counter, days: int) -> dict[str, set[int]]:
    """{term: indexes of the day's articles using it} for each bursting term.
    A name that's always in the news ("Trump") is left out even when it rises
    with a story: it's part of too many stories to define one."""
    common = {t for t in names(day) if usual[t] / days >= COMMON_NAME}
    used, carried = defaultdict(set), defaultdict(set)
    for k, a in enumerate(day):
        for t in words(a):
            used[t].add(k)
            carried[t].add(outlet(a))
    return {t: arts for t, arts in used.items()
            if len(arts) >= MIN_ARTICLES and len(carried[t]) >= MIN_OUTLETS and not t.isdigit()
            and t not in _CALENDAR and t.strip("'’") not in common
            and (len(arts) + 1) / (usual[t] / days + 1) >= MIN_LIFT}


def _together(burst: dict[str, set[int]]) -> list[set[str]]:
    """Bursting words that mostly appear in the same articles, as sets."""
    terms = list(burst)
    parent = {t: t for t in terms}

    def root(t):
        while parent[t] != t:
            t = parent[t]
        return t
    for i, t in enumerate(terms):
        for u in terms[i + 1:]:
            if len(burst[t] & burst[u]) >= TOGETHER * min(len(burst[t]), len(burst[u])):
                parent[root(t)] = root(u)
    sets = defaultdict(set)
    for t in terms:
        sets[root(t)].add(t)
    return list(sets.values())


@lru_cache(maxsize=1)
def _place_words() -> frozenset[str]:
    """The terms of every name that stands for a country ("north", "korea", "white", "house")."""
    return frozenset(t for n in place_names() for t in tokens(n))


def meaning(articles: list[dict]):
    """The articles' mean meaning vector (embed.py), unit length; None without the model."""
    import numpy as np

    import embed
    if not articles:
        return None
    v = embed.encode([embed.text(a) for a in articles])
    if v is None:
        return None
    v = v.mean(axis=0)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def _is_place(term: str) -> bool:
    """A term that names a country ("brazil", "israeli")."""
    return bool(places(term.strip("'’").title()))


def _country(group: list[dict]) -> Counter:
    return sum((where(a) for a in group), Counter())


def _home(mine: Counter, home: str | None) -> str | None:
    """The country an event belongs to: the story's own, unless the event is mostly about
    another (names it more than twice as often as the story's)."""
    if not mine:
        return home
    top, n = mine.most_common(1)[0]
    return home if mine.get(home, 0) * 2 >= n else top


def _name(events: list[list[str]], main: str, iso: str | None) -> str | None:
    """The phrase most of the story's events use for it (two or three words with its main
    word and something besides a place), as their headlines write it, led by its country
    when the phrase doesn't name it. None when no phrase is used by MIN_EVENTS events: what
    remains is a word some of the day's stories share ("president"), not one story."""
    grams, forms = Counter(), defaultdict(Counter)
    for titles in events:
        used = set()
        for title in titles:
            w = re.findall(r"[A-Za-z][\w’'-]*", title)
            for n in (2, 3):
                for i in range(len(w) - n + 1):
                    if w[i].lower() in _NOT_NAME or w[i + n - 1].lower() in _NOT_NAME:
                        continue
                    g = " ".join(w[i:i + n])
                    terms = tokens(g)
                    if main in terms and not set(terms) <= _place_words():
                        used.add(g.lower())
                        forms[g.lower()][g] += 1
        grams.update(used)
    if not grams or max(grams.values()) < MIN_EVENTS:
        return None
    # the most used phrase, or a longer one that contains it and is used nearly as often
    phrase, top = max(grams.items(), key=lambda kv: (kv[1], -len(kv[0])))
    longer = [g for g, c in grams.items() if phrase in g and g != phrase and c >= 0.8 * top]
    if longer:
        phrase = max(longer, key=grams.get)
    names = _country_names(iso)
    lead = not any(n.lower() in phrase for n in names)
    phrase = forms[phrase].most_common(1)[0][0]
    if names and lead:
        phrase = f"{names[0]} {phrase}"
    return phrase[0].upper() + phrase[1:]


def _country_names(iso: str | None) -> list[str]:
    """How a story's country can be named, the shortest common form first
    ("US", then "United States of America", "American")."""
    if not iso or not country(iso):
        return []
    split = lambda text: [n.strip() for n in (text or "").split(",") if n.strip()]  # noqa: E731
    aliases = [a for a in split(ALIASES.get(iso)) if a.isupper() and len(a) <= 4]
    return [*aliases, country(iso)["name"], *split(DEMONYMS.get(iso))]


def topics(day: list[dict], usual: Counter, days: int) -> list[dict]:
    """The day's running-story candidates, from its words alone (before any
    grouping): each {"words", "main", "own"}. Nothing without enough history."""
    if days < MIN_BASELINE_DAYS:
        return []
    burst = bursting(day, usual, days)
    out = []
    for story in _together(burst):
        if story <= _place_words():
            continue          # only where ("White House", "North Korea"), not what's happening
        main = max(story, key=lambda t: len(burst[t]))
        own = {t for t in story if t == main or len(burst[t] & burst[main]) >= OWN * len(burst[t])}
        out.append({"words": story, "main": main, "own": own})
    return out


def matches(article_words: set[str], topic: dict) -> bool:
    """An article uses two of a story's words, one of them its own, or three
    of them (a race call says "Senate", "race" and "wins", not "midterm")."""
    shared = article_words & topic["words"]
    return len(shared) >= 3 or (len(shared) >= 2 and bool(article_words & topic["own"]))


def find(groups: list[list[dict]], found_topics: list[dict]) -> list[dict]:
    """Running stories among a window's events (`groups`: lists of articles,
    as cluster() gives them), for the day's topics. Each is
    {"name", "iso", "events": indexes into groups, "words"}, biggest first."""
    if not groups or not found_topics:
        return []
    event_words = [[words(a) for a in g] for g in groups]
    found = []
    for topic in found_topics:
        vocabulary, main = topic["words"], topic["main"]
        members = [k for k, ws in enumerate(event_words) if sum(matches(w, topic) for w in ws) > JOIN * len(ws)]
        # ...and is about the same thing: near, in meaning, to the articles using the main word
        # (shared words alone let in a markets licence on a busy election day)
        core = meaning([a for k in members for a, ws in zip(groups[k], event_words[k], strict=True) if main in ws])
        if core is not None:
            members = [k for k in members if float(meaning(groups[k]) @ core) >= NEAR]
        if len(members) < MIN_EVENTS:
            continue
        # One country to a story: the one most named. An event mostly about another country goes
        # to that one, even when it names this one in passing ("South Africans vote", after the US's).
        named = {k: _country(groups[k]) for k in members}
        total = sum(named.values(), Counter())
        home = total.most_common(1)[0][0] if total else None
        by_country = defaultdict(list)
        for k in members:
            by_country[_home(named[k], home)].append(k)
        for iso, events in by_country.items():
            # named by the main word when its headlines use it, else by what they mostly say
            used = Counter(t for k in events for ws in event_words[k] for t in ws & vocabulary if not _is_place(t))
            arts = sum(len(groups[k]) for k in events)
            word = main if used[main] >= 0.3 * arts or not used else used.most_common(1)[0][0]
            shared = sum(outlets(groups[k]) >= 2 for k in events)
            if len(events) < MIN_EVENTS or shared < MIN_SHARED:
                continue
            # One big event and a few small ones that share its words (a sentencing and other
            # court cases, a train crash and other crashes) is that event, already a story.
            sizes = [len(groups[k]) for k in events]
            if max(sizes) > DOMINANT * sum(sizes):
                continue
            name = _name([[a["title"] for a in groups[k]] for k in events], word, iso)
            if name is None:
                continue
            found.append({"name": name, "iso": iso, "events": events, "words": sorted(vocabulary)})
    return sorted(found, key=lambda s: -outlets([a for k in s["events"] for a in groups[k]]))

