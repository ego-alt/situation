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
  freshness a story fades with age from when it broke (when a second outlet
            picked it up): full for 8 hours, then halving every 12, unless
            3+ outlets are still writing about it (the last 6 hours); and it
            fades once nobody has written about it for 12 hours
"""
import re
from datetime import UTC, datetime

from cluster import cosine, document, tfidf
from sources import BY_KEY

HOME_REGIONS = {"HK", "TW", "CN", "GB"}

MAJOR, HEADLINE, BACKGROUND = "major", "headline", "background"

ITN_BONUS = 2.0            # a match is worth about two more outlets
ITN_FLASH_OUTLETS = 4      # and makes a story FLASH only with this many outlets too

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
    return len(title.split()) >= 4 and not _NOT_A_HEADLINE.search(title)   # not a topic page ("Saudi Arabia")


def pick_headline(articles: list[dict]) -> str:
    """The most typical article's title, skipping live blogs and galleries."""
    for a in articles[:8]:
        if not _NOT_A_HEADLINE.search(a["title"]):
            return clean_title(a["title"])
    return clean_title(articles[0]["title"])


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
           itn_fresh: bool = True) -> dict:
    """Score and tier one story from its articles (most typical first).
    `itn_fresh`: the matched "In the news" item first appeared within a day."""
    per_outlet: dict[str, float] = {}
    leads = set()
    for a in articles:
        source = BY_KEY.get(a["source"])
        outlet = a.get("outlet") or a["source"]
        per_outlet[outlet] = max(per_outlet.get(outlet, 0.0), source.weight if source else 0.5)
        if source and source.ordered and a.get("position") is not None and a["position"] < 3:
            leads.add(outlet)
    breadth = round(sum(per_outlet.values()), 2)
    outlets = len(per_outlet)
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

    score = (breadth + 0.5 * len(leads) + (ITN_BONUS if itn_item else 0.0) + (1.0 if home else 0.0)) * freshness

    if is_soft(articles):
        tier = BACKGROUND
    elif (itn_item and itn_fresh and outlets >= ITN_FLASH_OUTLETS) or (outlets >= 6 and len(leads) >= 2):
        tier = MAJOR
    elif itn_item or outlets >= 4 or (outlets >= 3 and leads) or (home and breadth >= 2.0):
        tier = HEADLINE
    else:
        tier = BACKGROUND
    return {
        "headline": pick_headline([a for a in articles if hours(when(a)) <= HEADLINE_HOURS] or articles),
        "outlets": outlets, "breadth": breadth,
        "leads": len(leads), "home": home, "itn": itn_item, "score": round(score, 2), "tier": tier,
        "newest": newest,
    }
