"""Grouping articles about the same event.

Deterministic and dependency-free: TF-IDF vectors over each article's title
(counted twice) and the start of its summary, cosine similarity, and
single-link grouping above a threshold, then each group cut into the events
in it (see `split`). Capitalised words (places, people,
organisations) weigh more, because two headlines about one event share
names far more reliably than they share verbs.
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


# How much two articles must resemble each other to be linked.
THRESHOLD = 0.26
# How much a member must resemble its group's centroid to stay in it.
CORE = 0.3
# A linked group is first cut into tight cores: parts whose articles resemble
# each other, article for article on average, at least this much.
LINK = 0.15
# Cores are one story if they share what happened, not only who and where:
# their likeness counting only event words ("riot", "fire", "vote"), not names.
EVENT = 0.2
# ...and if they happen in the same place: cores mostly about different
# countries stay apart however alike their events (riots in France and riots
# in Italy). "Mostly": the country holds at least this share of the mentions.
PLACE_SHARE = 0.5


def where(article: dict) -> Counter:
    """The countries an article names, its headline counting three times."""
    found = Counter({iso: n * 3 for iso, n in places(article["title"]).items()})
    found.update(places(article.get("summary") or ""))
    return found


def _main_place(members: list[int], wheres: list[Counter]) -> str | None:
    total = sum((wheres[i] for i in members), Counter())
    if not total:
        return None
    iso, n = total.most_common(1)[0]
    return iso if n >= PLACE_SHARE * sum(total.values()) else None


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


def _centroid(members: list[int], vectors: list[dict], keep=lambda t: True) -> dict:
    c: dict[str, float] = {}
    for i in members:
        for t, x in vectors[i].items():
            if keep(t):
                c[t] = c.get(t, 0.0) + x
    norm = sum(x * x for x in c.values()) ** 0.5 or 1.0
    return {t: x / norm for t, x in c.items()}


def cores(members: list[int], vectors: list[dict]) -> list[list[int]]:
    """Average linkage: two parts join only if their articles resemble each
    other as a whole, so one article about two events can't fuse them."""
    parts = {i: [i] for i in members}
    total = {(a, b): cosine(vectors[a], vectors[b]) for n, a in enumerate(members) for b in members[n + 1:]}
    def pair(a, b):
        return (a, b) if (a, b) in total else (b, a)
    while len(parts) > 1:
        (a, b), best = max(((k, t / (len(parts[k[0]]) * len(parts[k[1]]))) for k, t in total.items()),
                           key=lambda kv: kv[1])
        if best < LINK:
            break
        parts[a] += parts.pop(b)
        for c in parts:
            if c != a:
                total[pair(a, c)] += total.pop(pair(b, c))
        del total[(a, b)]
    return list(parts.values())


def split(members: list[int], vectors: list[dict], named: set[str],
          wheres: list[Counter] | None = None) -> list[list[int]]:
    """A linked group as the stories in it. A fire at a base in Alberta and
    Alberta's referendum share names, and one article mentions both, but not
    what happened; the riots' cores share "riot" and "loot" however they're told,
    unless they're riots in two countries. Leftover articles join the story
    they are most about."""
    if len(members) < 4:
        return [members]
    parts = cores(members, vectors)
    big = [c for c in parts if len(c) >= 3]
    if len(big) < 2:
        return [members]
    event = [_centroid(c, vectors, lambda t: t not in named and not t.isdigit()) for c in big]
    place = [_main_place(c, wheres) if wheres else None for c in big]
    parent = list(range(len(big)))
    def find(i):
        while parent[i] != i:
            i = parent[i]
        return i
    for a in range(len(big)):
        for b in range(a + 1, len(big)):
            if place[a] and place[b] and place[a] != place[b]:
                continue
            if cosine(event[a], event[b]) >= EVENT:
                parent[find(a)] = find(b)
    stories: dict[int, list[int]] = {}
    for n, c in enumerate(big):
        stories.setdefault(find(n), []).extend(c)
    out = list(stories.values())
    whole = [_centroid(s, vectors) for s in out]
    for c in parts:
        if len(c) < 3:
            for i in c:
                out[max(range(len(out)), key=lambda n: cosine(vectors[i], whole[n]))].append(i)
    return out


def cluster(articles: list[dict], ongoing: list[str] | None = None) -> list[list[int]]:
    """Indexes of `articles` grouped into stories, biggest first, each group's
    most typical article first. Two layers (grouping.py), each a model learned
    from labels when one is saved (learn.py), else as below:
      1. events: events.Articles with the pairs model, or these hand rules (by_rules);
      2. situations: situations.Stories joins stories that are one situation
         (it uses Wikipedia's "Ongoing" items); without a model, none are."""
    import embed
    import learn
    pairs, joined = learn.load("pairs"), learn.load("situations")
    if not (pairs or joined):
        return by_rules(articles)
    import events
    meaning = embed.available() and bool((pairs or {}).get("embed") or (joined or {}).get("embed"))
    layer = events.Articles(articles, meaning=meaning)
    groups = layer.group(pairs) if pairs else by_rules(articles)
    if joined:
        import situations
        groups = situations.join(groups, joined, layer, ongoing or [])
    return groups


def by_rules(articles: list[dict]) -> list[list[int]]:
    """The hand rules for stage 1. Two articles from the same outlet are never
    joined directly (an outlet's own follow-ups would otherwise chain unrelated
    stories together); they can still meet through another outlet's article."""
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
            if cosine(v, vectors[j]) >= THRESHOLD:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(articles)):
        groups.setdefault(find(i), []).append(i)
    # Single links can chain: A~B and B~C put A and C together even when they
    # share nothing. Keep only members that resemble the group as a whole.
    out = []
    named = names(articles)
    wheres = [where(a) for a in articles]
    for members in (part for group in groups.values() for part in split(group, vectors, named, wheres)):
        if len(members) < 3:
            out.append(members)
            continue
        centroid = _centroid(members, vectors)
        fit = {i: cosine(vectors[i], centroid) for i in members}
        keep = sorted((i for i in members if fit[i] >= CORE), key=fit.get, reverse=True)
        out.append(keep or members[:1])   # most typical article first: it names the story
        out.extend([i] for i in members if i not in keep)
    return sorted(out, key=len, reverse=True)
