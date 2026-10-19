"""The outlets Situation reads, and Wikipedia's curated "In the news".

`weight` is how much an outlet's coverage counts toward a story's breadth
(1.0 for a major international desk). `region` marks outlets whose own
region gets a lower bar (see score.py).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    url: str
    weight: float = 1.0
    region: str | None = None
    outlet: str = ""          # feeds of one newsroom share it (two SCMP feeds are one outlet)

    def __post_init__(self):
        if not self.outlet:
            object.__setattr__(self, "outlet", self.key)


SOURCES = [
    # World desks: independent newsrooms on different continents.
    Source("bbc", "BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    Source("guardian", "The Guardian", "https://www.theguardian.com/world/rss"),
    Source("nyt", "New York Times", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    Source("aljazeera", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    Source("france24", "France 24", "https://www.france24.com/en/rss"),
    Source("dw", "DW", "https://rss.dw.com/rdf/rss-en-all"),
    Source("npr", "NPR", "https://feeds.npr.org/1004/rss.xml"),
    Source("lemonde", "Le Monde", "https://www.lemonde.fr/en/rss/une.xml"),
    Source("euronews", "Euronews", "https://www.euronews.com/rss"),
    # The region you live in: a lower bar for stories from here.
    Source("scmp", "SCMP", "https://www.scmp.com/rss/91/feed", 0.8, "asia"),
    Source("scmp-hk", "SCMP Hong Kong", "https://www.scmp.com/rss/2/feed", 0.6, "hk", outlet="scmp"),
    Source("rthk", "RTHK", "https://rthk9.rthk.hk/rthk/news/rss/e_expressnews_elocal.xml", 0.6, "hk"),
    Source("taipeitimes", "Taipei Times", "https://www.taipeitimes.com/xml/index.rss", 0.6, "tw"),
    Source("cna", "CNA", "https://www.channelnewsasia.com/rssfeeds/8395986", 0.8, "asia"),
]

BY_KEY = {s.key: s for s in SOURCES}

# Wikipedia's "In the news" box: a few items a day picked by editors with a
# high, global bar, plus an "Ongoing" list of long-running situations.
ITN_URL = ("https://en.wikipedia.org/w/api.php?action=parse&page=Template:In_the_news"
           "&prop=text&format=json&formatversion=2")

USER_AGENT = "situation/0.1 (personal news filter; home stack)"
