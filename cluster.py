"""Stage 2 of the pipeline: which news articles report the same event
(docs/phases.md). Two methods, chosen by cluster():

  by_rules    the hand rules (deployed): TF-IDF likeness over each article's
              title (counted twice) and the start of its summary (text.py),
              single-link grouping above a threshold, then each group cut
              into the events in it (see `split`)
  by_meaning  off the shelf: average linkage on meaning vectors (embed.py),
              switched on by instance/models/meaning.json
"""
from collections import Counter

from text import cosine, document, names, tfidf, where

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


def _main_place(members: list[int], wheres: list[Counter]) -> str | None:
    total = sum((wheres[i] for i in members), Counter())
    if not total:
        return None
    iso, n = total.most_common(1)[0]
    return iso if n >= PLACE_SHARE * sum(total.values()) else None


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


def cluster(articles: list[dict]) -> list[list[int]]:
    """Indexes of `articles` grouped into events, biggest first, each group's
    most typical article first: by meaning when a "meaning" model is saved
    (learn.py, by_meaning), else by the hand rules (by_rules)."""
    import embed
    import learn
    meaning = learn.load("meaning")
    if meaning and embed.available():
        return by_meaning(articles, meaning["cut"])
    return by_rules(articles)


def by_meaning(articles: list[dict], cut: float) -> list[list[int]]:
    """Off the shelf: average-linkage clustering of the articles' meaning
    vectors (embed.py), cut where groups are on average further apart than
    `cut` (cosine distance). Switched on by instance/models/meaning.json
    ({"cut": ...}); each group's most typical article (nearest the rest) first."""
    import numpy as np
    from scipy.cluster.hierarchy import fcluster, linkage

    import embed
    if len(articles) < 2:
        return [[i] for i in range(len(articles))]
    vectors = embed.encode([embed.text(a) for a in articles])
    labels = fcluster(linkage(vectors, method="average", metric="cosine"), t=cut, criterion="distance")
    groups: dict[int, list[int]] = {}
    for i, c in enumerate(labels.tolist()):
        groups.setdefault(c, []).append(i)
    out = []
    for g in groups.values():
        sims = vectors[g] @ vectors[g].T
        out.append([g[k] for k in np.argsort(-sims.sum(axis=1), kind="stable")])
    return sorted(out, key=len, reverse=True)


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
