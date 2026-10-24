"""Stage 2 of grouping: which stories (stage 1's tight groups) are one situation.

Stage 1 (learn.group, or the hand rules in cluster.py) groups articles that
report the same event. A situation is wider: the Russia-Ukraine war takes in
peace talks, an offensive and the Pope's appeal for a ceasefire, whose
headlines share few words. One headline against another can't see that; one
story against another can, from much more evidence:

  meaning           how close the stories' average meanings are (embed.py)
  words             how alike their combined words are
  names             the people and organisations both name (Witkoff, Houthi),
                    how many, and what share of each story's names
  countries         the same main country; how much their countries overlap
  ongoing           both match the same item in Wikipedia's "Ongoing" list
                    (its editors name the wars and crises: our stand-in for a
                    knowledge base), or two different items
  decisive          exactly one of them reads as a decisive moment (a result,
                    a ceasefire signed, a leader ousted), which stays apart
  time, size        hours between them; how big each is

A logistic regression (learn.fit_logistic) turns those into the chance two
stories are one situation, trained on Claude's labels (see examples); stories
then join by average linkage, as articles do in stage 1. It works on a few
hundred stories, not thousands of articles, so it costs little.
"""
import math
import re
from collections import Counter

import numpy as np

import learn
from cluster import cosine, tfidf, tokens, where
from geo import places

FEATURES = ["meaning", "words", "names_share", "shared_names", "same_country", "country_overlap",
            "same_ongoing", "other_ongoing", "decisive", "hours_apart", "smaller", "larger"]
NEIGHBOURS = 25        # candidate stories per story: the most alike in meaning or words
MERGE = 0.5            # average probability at which two stories are one situation

# Headlines that read as a decisive moment: their own story under the rubric.
DECISIVE = re.compile(
    r"\b(wins?|won|elected|re-elected|concedes?|results?|projected|votes? (?:to|for|against)|reject(?:s|ed)?|"
    r"verdict|convicted|acquitted|sentenced|ceasefire (?:agreed|signed|deal)|peace (?:deal|agreement) signed|"
    r"resigns?|resigned|ousted|toppled|assassinated|dies|died|killed in|declares? war|invades?|invasion begins)\b",
    re.I)


class Story:
    """What stage 2 knows about one stage-1 group."""

    def __init__(self, members: list[int], ctx: "learn.Pairs", topics: list[tuple[set, set, dict]]):
        arts = [ctx.articles[i] for i in members]
        self.members = members
        self.size = len(members)
        self.words = _unit_sum(ctx.vectors[i] for i in members)
        if ctx.meaning is not None:
            v = ctx.meaning[members].mean(axis=0)
            n = float(np.linalg.norm(v))
            self.meaning = v / n if n else v
        else:
            self.meaning = None
        self.names = Counter(t for i in members for t in ctx.vectors[i] if t in ctx.named)
        countries = sum((where(a) for a in arts), Counter())
        total = sum(countries.values())
        self.country = countries.most_common(1)[0][0] if countries else None
        self.countries = {c for c, n in countries.items() if n >= 0.2 * total}
        self.start, self.end = min(ctx.time[i] for i in members), max(ctx.time[i] for i in members)
        self.decisive = sum(bool(DECISIVE.search(a["title"])) for a in arts) * 2 > len(arts)
        self.ongoing = _ongoing(self, topics)


def _unit_sum(vectors) -> dict:
    out: dict[str, float] = {}
    for v in vectors:
        for t, x in v.items():
            out[t] = out.get(t, 0.0) + x
    norm = math.sqrt(sum(x * x for x in out.values())) or 1.0
    return {t: x / norm for t, x in out.items()}


def topics_of(ongoing: list[str]) -> list[tuple[set, set, dict]]:
    """Wikipedia's "Ongoing" items as (countries, words, vector)."""
    out = []
    for topic in ongoing or []:
        isos = set(places(topic.replace("Russo-Ukrainian", "Russian Ukrainian")))
        out.append((isos, set(tokens(topic)), tfidf([tokens(topic)])[0]))
    return out


def _ongoing(story: Story, topics) -> int | None:
    """The "Ongoing" item a story is part of: one whose countries include the
    story's main country (the likeliest of them by words), or None."""
    best = None
    for k, (isos, _, vector) in enumerate(topics):
        if story.country in isos:
            score = cosine(vector, story.words)
            if best is None or score > best[0]:
                best = (score, k)
    return best[1] if best else None


def features(a: Story, b: Story) -> list[float]:
    shared = a.names.keys() & b.names.keys()
    names_share = (sum(min(a.names[t], b.names[t]) for t in shared)
                   / (sum(max(a.names[t], b.names[t]) for t in a.names.keys() | b.names.keys()) or 1))
    gap = max(0.0, max(a.start, b.start) - min(a.end, b.end)) / 3600
    return [
        float(a.meaning @ b.meaning) if a.meaning is not None and b.meaning is not None else 0.0,
        cosine(a.words, b.words),
        names_share,
        math.log1p(len(shared)),
        float(bool(a.country and a.country == b.country)),
        len(a.countries & b.countries) / (len(a.countries | b.countries) or 1),
        float(a.ongoing is not None and a.ongoing == b.ongoing),
        float(a.ongoing is not None and b.ongoing is not None and a.ongoing != b.ongoing),
        float(a.decisive != b.decisive),
        math.log1p(gap),
        math.log1p(min(a.size, b.size)),
        math.log1p(max(a.size, b.size)),
    ]


def candidates(stories: list[Story]) -> set[tuple[int, int]]:
    """Pairs worth asking about: each story's NEIGHBOURS most alike in meaning
    (or words, without meaning), among those sharing a country, a name or an
    "Ongoing" item. Stories with nothing in common aren't scored."""
    n = len(stories)
    if n < 2:
        return set()
    if all(s.meaning is not None for s in stories):
        m = np.array([s.meaning for s in stories])
        sims = m @ m.T
    else:
        sims = np.array([[cosine(a.words, b.words) for b in stories] for a in stories])
    out = set()
    for i, a in enumerate(stories):
        order = np.argsort(-sims[i])
        picked = 0
        for j in order:
            j = int(j)
            if j == i:
                continue
            b = stories[j]
            related = (a.countries & b.countries or a.names.keys() & b.names.keys()
                       or (a.ongoing is not None and a.ongoing == b.ongoing))
            if related:
                out.add((min(i, j), max(i, j)))
                picked += 1
                if picked >= NEIGHBOURS:
                    break
    return out


def stories_of(groups: list[list[int]], ctx: "learn.Pairs", ongoing: list[str]) -> list[Story]:
    topics = topics_of(ongoing)
    return [Story(g, ctx, topics) for g in groups]


def join(groups: list[list[int]], model: dict, ctx: "learn.Pairs", ongoing: list[str]) -> list[list[int]]:
    """Stage 1's groups joined into situations: biggest first, each led by its
    biggest story's most typical article (it names the situation)."""
    stories = stories_of(groups, ctx, ongoing)
    pairs = sorted(candidates(stories))
    if not pairs:
        return groups
    x = np.array([features(stories[i], stories[j]) for i, j in pairs])
    p = learn.predict_logistic(model, x[:, [FEATURES.index(f) for f in model["features"]]])
    near: dict[int, dict[int, float]] = {i: {} for i in range(len(stories))}
    for (i, j), q in zip(pairs, p.tolist(), strict=True):
        near[i][j] = near[j][i] = q
    joined = learn._agglomerate(list(range(len(stories))), near, lambda q: q, lambda total, n: total / n, MERGE)
    out = []
    for parts in joined:
        parts.sort(key=lambda k: -stories[k].size)
        out.append([i for k in parts for i in stories[k].members])
    return sorted(out, key=len, reverse=True)


def examples(groups: list[list[int]], ctx: "learn.Pairs", ongoing: list[str],
             judged: dict[frozenset, bool]) -> list[tuple[list[float], bool]]:
    """Training pairs: candidate pairs of stories with what the labels say about
    them, the majority of the judged pairs of their articles (pairs of stories
    with none judged are left out)."""
    stories = stories_of(groups, ctx, ongoing)
    url = [a["url"] for a in ctx.articles]
    out = []
    for i, j in candidates(stories):
        votes = [judged[k] for a in stories[i].members for b in stories[j].members
                 if (k := frozenset((url[a], url[b]))) in judged]
        if votes:
            out.append((features(stories[i], stories[j]), sum(votes) * 2 > len(votes)))
    return out


def fit(examples: list[tuple[list[float], bool]]) -> dict | None:
    if len({y for _, y in examples}) < 2:
        return None
    import embed
    x = np.array([f for f, _ in examples])
    y = np.array([float(s) for _, s in examples])
    return {"features": FEATURES, "embed": embed.available(), **learn.fit_logistic(x, y)}
