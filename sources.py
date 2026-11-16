"""The outlets Situation reads, Wikipedia's curated "In the news", and how anything it reads is
asked for (get) and made safe to put in a page (clean).

`weight` is how much an outlet's coverage counts toward a story's breadth
(1.0 for a major international desk). `ordered` marks feeds listed in the
editors' front-page order; only their top items count as a story's "lead".
Most feeds are simply newest first, where a top spot means nothing more
than recent. `via_google` marks outlets read through Google News's search
feed, because they have none of their own (Reuters, AP): its items carry no
summary, and their headlines end in " - Reuters". `skip` is a pattern for
links that aren't articles (a feed's video clips).
"""
import html
import re
from dataclasses import dataclass

import requests


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    url: str
    weight: float = 1.0
    ordered: bool = False
    outlet: str = ""
    via_google: bool = False
    skip: str = ""          # feeds of one newsroom share it (two SCMP feeds are one outlet)

    def __post_init__(self):
        if not self.outlet:
            object.__setattr__(self, "outlet", self.key)


SOURCES = [
    # The wire services, which most newsrooms build on (no RSS of their own any more).
    Source("reuters", "Reuters", "https://news.google.com/rss/search?q=when:1d+site:reuters.com/world"
           "&hl=en-US&gl=US&ceid=US:en", via_google=True),
    Source("ap", "AP", "https://news.google.com/rss/search?q=when:1d+site:apnews.com"
           "&hl=en-US&gl=US&ceid=US:en", via_google=True),
    # World desks: independent newsrooms on different continents.
    Source("bbc", "BBC", "https://feeds.bbci.co.uk/news/world/rss.xml", ordered=True),
    Source("guardian", "The Guardian", "https://www.theguardian.com/world/rss", ordered=True),
    Source("nyt", "New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", ordered=True),
    Source("aljazeera", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    Source("france24", "France 24", "https://www.france24.com/en/rss"),
    Source("dw", "DW", "https://rss.dw.com/rdf/rss-en-top"),         # top stories, not "all" (sport, culture)
    Source("npr", "NPR", "https://feeds.npr.org/1004/rss.xml"),
    Source("lemonde", "Le Monde", "https://www.lemonde.fr/en/rss/une.xml", ordered=True),
    Source("euronews", "Euronews", "https://www.euronews.com/rss"),
    Source("bloomberg", "Bloomberg", "https://feeds.bloomberg.com/politics/news.rss", 0.8, skip=r"/videos?/"),
    # The region you live in (the lower bar itself is in score.HOME_REGIONS).
    Source("scmp", "SCMP", "https://www.scmp.com/rss/91/feed", 0.8),
    Source("scmp-hk", "SCMP Hong Kong", "https://www.scmp.com/rss/2/feed", 0.6, outlet="scmp"),
    Source("rthk", "RTHK", "https://rthk9.rthk.hk/rthk/news/rss/e_expressnews_elocal.xml", 0.6),
    Source("taipeitimes", "Taipei Times", "https://www.taipeitimes.com/xml/index.rss", 0.6),
    Source("cna", "CNA", "https://www.channelnewsasia.com/rssfeeds/8395986", 0.8),
]

BY_KEY = {s.key: s for s in SOURCES}


def outlet(article: dict) -> str:
    """The newsroom an article is from (its feed, unless the newsroom has several)."""
    return article.get("outlet") or article["source"]


# Wikipedia's "In the news" box: a few items a day picked by editors with a
# high, global bar, plus an "Ongoing" list of long-running situations.
ITN_URL = ("https://en.wikipedia.org/w/api.php?action=parse&page=Template:In_the_news"
           "&prop=text&format=json&formatversion=2")

USER_AGENT = "situation/0.1 (personal news filter; home stack)"
TIMEOUT = 20        # seconds a source has to answer


def get(url: str, timeout: float = TIMEOUT, **params) -> requests.Response:
    """What a source answers, asked as Situation asks everything it reads; raises when it
    doesn't answer in `timeout` seconds, or answers with an error."""
    resp = requests.get(url, params=params or None, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp


_KEEP = re.compile(r"(</?(?:i|sub|sup)>)", re.I)


def clean(raw: str | None) -> str:
    """Plain text, safe to put in a page, keeping only italics, sub- and superscripts."""
    text = html.unescape(raw or "")
    text = re.sub(r"<(/?)em>", r"<\1i>", text, flags=re.I)
    # Reference numbers ("well-being<sup>1</sup>", "circuits<sup>3–5</sup>") point to a list that isn't here.
    text = re.sub(r"(?<=[^\d\s])<sup>[\d,\u2013\u2012-]+</sup>", "", text, flags=re.I)
    out = []
    for part in _KEEP.split(text):
        if _KEEP.fullmatch(part):
            out.append(part.lower())
        else:
            out.append(html.escape(re.sub(r"<[^>]*>", " ", part), quote=False))
    return re.sub(r"\s+", " ", "".join(out)).strip()
