"""How important a story is, and which tier that puts it in.

Signals (see docs/plan.md):
  breadth   distinct outlets carrying it, each counted once at its weight
  lead      outlets that put it among their top three items (front-page-ordered
            feeds only; in a newest-first feed the top spot just means recent)
  curated   it matches an item in Wikipedia's "In the news": confirmation, not a
            trump card. It adds ITN_BONUS, lifts a story with few outlets to
            PRIORITY (catching what these feeds under-cover), and makes it
            FLASH only with ITN_FLASH_OUTLETS+ outlets, while the item is new
  region    it happens in a place you live or follow
  copies    an outlet running a wire's article word for word (CNA's Reuters
            copy) adds no vote of its own; the wire already counts
  the bar   tier thresholds are set for REFERENCE_OUTLETS newsrooms and scale
            with how many published today, so adding a feed doesn't lower it
  freshness a story fades with age from when it broke (when a second outlet
            picked it up): full for 8 hours, then halving every 12, unless
            3+ outlets are still writing about it (the last 6 hours); and it
            fades once nobody has written about it for 12 hours
"""
import re
from collections import Counter
from datetime import UTC, datetime

from geo import places
from sources import BY_KEY
from text import cosine, document, tfidf, tokens, where

HOME_REGIONS = {"HK", "TW", "CN", "GB"}

MAJOR, HEADLINE, BACKGROUND = "major", "headline", "background"

ITN_BONUS = 2.0            # a match is worth about two more outlets
ITN_FLASH_OUTLETS = 4      # and makes a story FLASH only with this many outlets too

# The outlet counts below were tuned with this many newsrooms publishing (18–19 Oct 2026).
REFERENCE_OUTLETS = 13
WIRES = {"ap", "reuters"}
COPY = 0.85               # title likeness (shared words over all words) that means a wire's copy


def need(outlets: int, active: int | None) -> int:
    """An outlet threshold scaled to the newsrooms publishing today."""
    return outlets if not active else max(2, round(outlets * active / REFERENCE_OUTLETS))


def copies(articles: list[dict]) -> set[str]:
    """Outlets whose article here is a wire's, word for word."""
    terms = [(a.get("outlet") or a["source"], a["source"] in WIRES, set(tokens(a["title"]))) for a in articles]
    wire = [t for _, is_wire, t in terms if is_wire and len(t) >= 5]
    return {o for o, is_wire, t in terms
            if not is_wire and len(t) >= 5 and any(len(t & w) / len(t | w) >= COPY for w in wire)}

# Sport and entertainment: news, but not the kind this app is for. A story most
# of whose headlines match stays below the bar, even when Wikipedia's "In the
# news" carries it (it lists recurring finals and tournaments). Edit freely.
_SOFT = re.compile(
    r"\b(football|soccer|premier league|champions league|la liga|serie a|bundesliga|fifa|uefa|"
    r"world cup|olympics?|paralympics?|tennis|wimbledon|grand slam|cricket|rugby|golf|nba|nfl|mlb|nhl|"
    r"formula (?:one|1)|f1|grand prix|boxing|ufc|marathon|transfer window|striker|midfielder|goalkeeper|"
    r"coach|playoffs?|ronaldo|messi|celebrity|celebrities|actor|actress|singer|rapper|pop star|album|"
    r"box office|oscars?|grammys?|emmys?|bafta|eurovision|fashion week|red carpet|k-pop|reality (?:tv|show))\b",
    re.I)


def is_soft(articles: list[dict]) -> bool:
    """Most of a story's headlines are about sport or entertainment."""
    hits = sum(bool(_SOFT.search(a["title"])) for a in articles)
    return hits * 2 >= len(articles) and hits > 0


# Freshness (see the module docstring).
FRESH_HOURS = 8
HALF_LIFE_HOURS = 12
OLD_FLOOR = 0.25
DEVELOPING_HOURS = 6
DEVELOPING_OUTLETS = 3
DEVELOPING_FLOOR = 0.75

# Titles that name a format, not the news: live blogs, galleries, explainers.
_NOT_A_HEADLINE = re.compile(
    r"\b(live|watch|replay|in pictures|photos?|video|what to know|what we know|explainer|opinion|"
    r"analysis|podcast|newsletter|quiz)\b|\?$", re.I)
_EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿️]+")


def clean_title(title: str) -> str:
    title = _EMOJI.sub("", title)
    title = re.sub(r"^\s*(live|breaking|watch|replay)\s*[:|-]\s*", "", title, flags=re.I)
    title = re.sub(r"\s*[-–—|:]\s*the latest\s*$", "", title, flags=re.I)   # AP's running stories
    return re.sub(r"\s+", " ", title).strip(" -–—|:")


HEADLINE_HOURS = 12   # a story's headline comes from its last half day, so it moves on


def is_headline(title: str) -> bool:
    """A title that states news (not a live blog, gallery, explainer or question)."""
    return len(title.split()) >= 3 and not _NOT_A_HEADLINE.search(title)   # not a topic page ("Saudi Arabia")


# Not live news: commentary, explainers, retrospectives and pages that aren't
# articles. Left out of grouping and the page (the database keeps them). Live
# blogs and videos of what's happening stay: they're live news. Checked against
# Claude on 2,000 articles (October 2026): about a quarter aren't live news;
# this catches a fifth of those, wrongly dropping one news item in ten it
# drops. Headlines alone can't tell a feature from news (an embedding
# classifier did no better); the article body could.
_NOT_NEWS = re.compile(
    r"\b(opinion|analysis|comment|commentary|editorial|editorial cartoon|letters?(?: to the editor)?|column|"
    r"explainer|explained|what to know|what we know|what you need to know|q&a|fact ?check|"
    r"podcast|newsletter|quiz|crossword|in pictures|photos? of the (?:day|week)|gallery|"
    r"the week in|year in review|a look back|timeline|scores?, news (?:&|and) stats|evening news|morning brief(?:ing)?|"
    r"first thing|today in history|horoscope)\b|\?\s*$", re.I)


def is_news(article: dict) -> bool:
    """A report of something happening: not opinion, analysis, an explainer, a
    look back, a question or a page that isn't an article (a topic page,
    scores, a TV bulletin)."""
    title = article["title"]
    return len(title.split()) >= 3 and not _NOT_NEWS.search(title) and "/opinion" not in article.get("url", "")


def news_only(items: list[dict]) -> list[dict]:
    return [a for a in items if is_news(a)]


def pick_headline(articles: list[dict], place: str | None = None) -> str:
    """The most typical article's title, skipping live blogs and galleries,
    and, when the story has a main country, titles that name only another."""
    def fits(a):
        named = places(a["title"])
        return not place or not named or place in named
    for a in articles[:8]:
        if is_headline(a["title"]) and fits(a):
            return clean_title(a["title"])
    for a in articles[:8]:
        if is_headline(a["title"]):
            return clean_title(a["title"])
    return clean_title(articles[0]["title"])


def _main_country(articles: list[dict]) -> str | None:
    total = sum((where(a) for a in articles), Counter())
    return total.most_common(1)[0][0] if total else None


def match_itn(groups_articles: list[list[dict]], itn_items: list[str], threshold: float = 0.2) -> dict:
    """{group index: ITN item} for groups that an ITN item describes: the best
    match between the item and any one of a story's articles. Only stories
    carried by two or more outlets qualify, so an item never lands on a lone
    opinion piece that happens to share its words."""
    if not itn_items or not groups_articles:
        return {}
    eligible = [i for i, arts in enumerate(groups_articles)
                if len({a.get("outlet") or a["source"] for a in arts}) >= 2]
    owners, docs = [], []
    for i in eligible:
        for a in groups_articles[i][:15]:
            owners.append(i)
            docs.append(document(a))
    vectors = tfidf(docs + [document({"title": item}) for item in itn_items])
    article_v, itn_v = vectors[: len(docs)], vectors[len(docs):]
    matches = {}
    for k, item in enumerate(itn_items):
        best, owner = max(((cosine(itn_v[k], v), owners[j]) for j, v in enumerate(article_v)), default=(0, None))
        if owner is not None and best >= threshold and owner not in matches:
            matches[owner] = item
    return matches


def assess(articles: list[dict], place: dict | None, itn_item: str | None, now: datetime,
           itn_fresh: bool = True, active: int | None = None) -> dict:
    """Score and tier one story from its articles (most typical first).
    `itn_fresh`: the matched "In the news" item first appeared within a day.
    `active`: newsrooms that published today, to scale the bar (None: unscaled)."""
    per_outlet: dict[str, float] = {}
    leads = set()
    for a in articles:
        source = BY_KEY.get(a["source"])
        outlet = a.get("outlet") or a["source"]
        per_outlet[outlet] = max(per_outlet.get(outlet, 0.0), source.weight if source else 0.5)
        if source and source.ordered and a.get("position") is not None and a["position"] < 3:
            leads.add(outlet)
    outlets = len(per_outlet)                       # shown: everyone carrying it
    for o in copies(articles):
        per_outlet.pop(o, None)
        leads.discard(o)
    breadth = round(sum(per_outlet.values()), 2)
    votes = len(per_outlet)                         # counted: independent newsrooms
    home = bool(place and place["iso"] in HOME_REGIONS)

    def when(a):
        t = a.get("published") or now
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    times = [when(a) for a in articles if a.get("published")]
    newest = max(times) if times else now
    first_by_outlet: dict[str, datetime] = {}
    for a in articles:
        outlet = a.get("outlet") or a["source"]
        first_by_outlet[outlet] = min(first_by_outlet.get(outlet, when(a)), when(a))
    starts = sorted(first_by_outlet.values())
    broke = starts[1] if len(starts) > 1 else starts[0] if starts else now
    hours = lambda t: max(0.0, (now - t).total_seconds() / 3600)  # noqa: E731
    recent = {a.get("outlet") or a["source"] for a in articles if hours(when(a)) <= DEVELOPING_HOURS}
    age = hours(broke)
    fade = 1.0 if age <= FRESH_HOURS else max(OLD_FLOOR, 0.5 ** ((age - FRESH_HOURS) / HALF_LIFE_HOURS))
    if len(recent) >= DEVELOPING_OUTLETS:
        fade = max(fade, DEVELOPING_FLOOR)          # still being written about: keep it up
    quiet = hours(newest)
    silence = 1.0 if quiet <= 12 else max(0.3, 1.0 - (quiet - 12) / 50)
    freshness = min(fade, silence)

    parts = {"breadth": breadth, "leads": len(leads), "itn": bool(itn_item), "home": home, "freshness": freshness}
    score = sum(w * x for w, x in zip(weights().values(), rank_features(parts), strict=True))

    if is_soft(articles):
        tier = BACKGROUND
    elif ((itn_item and itn_fresh and votes >= need(ITN_FLASH_OUTLETS, active))
          or (votes >= need(6, active) and len(leads) >= 2)):
        tier = MAJOR
    elif itn_item or votes >= need(4, active) or (votes >= need(3, active) and leads) or (home and breadth >= 2.0):
        tier = HEADLINE
    else:
        tier = BACKGROUND
    recent_articles = [a for a in articles if hours(when(a)) <= HEADLINE_HOURS]
    return {
        "headline": pick_headline(recent_articles or articles, _main_country(articles)),
        "outlets": outlets, "breadth": breadth,
        "leads": len(leads), "home": home, "itn": itn_item, "score": round(score, 2), "tier": tier,
        "newest": newest, "parts": parts,
    }


def weights() -> dict:
    """What each part of the score is worth: learned from your "which matters
    more" answers when there's a model (learn.py), else set by hand."""
    import learn
    model = learn.load("rank")
    return model["weights"] if model else {"breadth": 1.0, "lead": 0.5, "itn": ITN_BONUS, "home": 1.0}


def rank_features(parts: dict) -> list[float]:
    """The score's parts, breadth, lead, Wikipedia and home region, each times freshness."""
    f = parts["freshness"]
    return [parts["breadth"] * f, parts["leads"] * f, float(parts["itn"]) * f, float(parts["home"]) * f]
