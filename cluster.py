"""Grouping articles about the same event.

Deterministic and dependency-free: TF-IDF vectors over each article's title
(counted twice) and the start of its summary, cosine similarity, and
single-link grouping above a threshold. Capitalised words (places, people,
organisations) weigh more, because two headlines about one event share
names far more reliably than they share verbs.
"""
import math
import re
from collections import Counter

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
    """Weighted terms: names (capitalised, not sentence-initial) count double."""
    out = Counter()
    words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]+|\d{2,}", text or "")
    for i, raw in enumerate(words):
        low = raw.lower().strip("'’-")
        if low in STOPWORDS or len(low) < 3:
            continue
        name = raw[0].isupper() and i > 0
        out[_stem(low)] += 2 if name else 1
    return out


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


# How much a member must resemble its group's centroid to stay in it.
CORE = 0.3


def cluster(articles: list[dict], threshold: float = 0.26) -> list[list[int]]:
    """Indexes of `articles`, grouped by event, each group's most typical
    article first. Two articles from the same
    outlet are never joined directly (an outlet's own follow-ups would
    otherwise chain unrelated stories together); they can still meet through
    another outlet's article."""
    vectors = tfidf([document(a) for a in articles])
    parent = list(range(len(articles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    index: dict[str, list[int]] = {}
    for i, v in enumerate(vectors):
        for t in v:
            index.setdefault(t, []).append(i)
    for i, v in enumerate(vectors):
        candidates = {j for t in v for j in index[t] if j > i}
        for j in candidates:
            if articles[i].get("outlet", articles[i]["source"]) == articles[j].get("outlet", articles[j]["source"]):
                continue
            if cosine(v, vectors[j]) >= threshold:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(articles)):
        groups.setdefault(find(i), []).append(i)
    # Single links can chain: A~B and B~C put A and C together even when they
    # share nothing. Keep only members that resemble the group as a whole.
    out = []
    for members in groups.values():
        if len(members) < 3:
            out.append(members)
            continue
        centroid: dict[str, float] = {}
        for i in members:
            for t, x in vectors[i].items():
                centroid[t] = centroid.get(t, 0.0) + x
        norm = sum(x * x for x in centroid.values()) ** 0.5 or 1.0
        centroid = {t: x / norm for t, x in centroid.items()}
        fit = {i: cosine(vectors[i], centroid) for i in members}
        keep = sorted((i for i in members if fit[i] >= CORE), key=fit.get, reverse=True)
        out.append(keep or members[:1])   # most typical article first: it names the story
        out.extend([i] for i in members if i not in keep)
    return sorted(out, key=len, reverse=True)
