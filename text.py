"""Text as the grouping and scoring see it: weighted terms, TF-IDF vectors,
cosine likeness, the names a set of articles uses, and the countries an
article names. Shared by cluster.py, score.py and the tools.
"""
import math
import re
from collections import Counter
from functools import lru_cache

from geo import places

STOPWORDS = set("""
a an the and or but of to in on at for from by with as is are was were be been being it its
this that these those after before over under into about amid than then there their they he she
his her him we our you your i not no new says say said will would could can may might has have
had do does did more most up out off just also how why what who when where which while week day
year years first last two three one other some all any us its it's over-the after-the latest
live update updates news report reports video watch photos analysis opinion explainer
""".split())


def _stem(word: str) -> str:
    for suffix in ("ings", "ing", "ers", "ies", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def tokens(text: str) -> Counter:
    """Weighted terms: names (capitalised, not sentence-initial) count double.
    Cached by text: every fetch re-reads the same articles."""
    return Counter(dict(_tokens(text or "")))


@lru_cache(maxsize=50_000)
def _tokens(text: str) -> tuple:
    out = Counter()
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]+|\d{2,}", text or "")
    for i, raw in enumerate(words):
        low = raw.lower().strip("'’-")
        if low in STOPWORDS or len(low) < 3:
            continue
        name = raw[0].isupper() and i > 0
        out[_stem(low)] += 2 if name else 1
    return tuple(out.items())


def document(article: dict) -> Counter:
    title = tokens(article["title"])
    summary = tokens(" ".join((article.get("summary") or "").split()[:40]))
    return title + title + summary   # the title is what the story is about


def tfidf(docs: list[Counter]) -> list[dict]:
    n = len(docs)
    df = Counter(term for d in docs for term in d)
    vectors = []
    for d in docs:
        v = {t: (1 + math.log(c)) * math.log((1 + n) / (1 + df[t])) for t, c in d.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vectors.append({t: x / norm for t, x in v.items()})
    return vectors


def cosine(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def where(article: dict) -> Counter:
    """The countries an article names, its headline counting three times."""
    found = Counter({iso: n * 3 for iso, n in places(article["title"]).items()})
    found.update(places(article.get("summary") or ""))
    return found


def names(articles: list[dict]) -> set[str]:
    """Terms the coverage mostly capitalises mid-sentence: people and places."""
    upper, lower = Counter(), Counter()
    for a in articles:
        for text in (a["title"], a.get("summary") or ""):
            words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]+", text)
            if sum(w[0].isupper() for w in words) > 0.6 * len(words):
                continue   # Title Case, or empty: says nothing about which words are names
            for w in words[1:]:
                (upper if w[0].isupper() else lower)[_stem(w.lower().strip("'’-"))] += 1
    return {t for t in upper if upper[t] > lower[t]}
