"""How well a grouping matches the truth. Pure functions over article urls: no
database, no app, so any tool can use them.

  truth   each article's true event: {url: event id}
  found   each article's group in the grouping being scored: {url: group id}

B-cubed scores each article, then averages over articles, so every article
counts once however big its event (counting pairs lets one huge story outweigh
a hundred small ones):

  precision  of the articles grouped with it, the share truly in its event
  recall     of the articles truly in its event, the share grouped with it

Headline-size events are those carried by HEADLINE_OUTLETS or more outlets:
the ones the page can show, where grouping matters most.
"""
import random

HEADLINE_OUTLETS = 3


def bcubed(truth: dict[str, object], found: dict[str, object]) -> dict[str, tuple[float, float]]:
    """Each article's (precision, recall). Articles without a true event aren't
    scored, and don't count against others."""
    scored = [u for u in truth if u in found]
    groups: dict[object, list[str]] = {}
    for u in scored:
        groups.setdefault(found[u], []).append(u)
    sizes: dict[object, int] = {}
    for u in scored:
        sizes[truth[u]] = sizes.get(truth[u], 0) + 1
    parts = {}
    for members in groups.values():
        counts: dict[object, int] = {}
        for u in members:
            counts[truth[u]] = counts.get(truth[u], 0) + 1
        for u in members:
            parts[u] = (counts[truth[u]] / len(members), counts[truth[u]] / sizes[truth[u]])
    return parts


def summary(parts) -> dict:
    """Mean precision and recall over articles, and their F1."""
    parts = list(parts)
    if not parts:
        return {"articles": 0, "precision": None, "recall": None, "f1": None}
    p = sum(x for x, _ in parts) / len(parts)
    r = sum(y for _, y in parts) / len(parts)
    return {"articles": len(parts), "precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def headline_articles(truth: dict[str, object], outlet: dict[str, str]) -> set[str]:
    """Articles in true events that HEADLINE_OUTLETS or more outlets carry
    (`outlet`: each article's outlet)."""
    outlets: dict[object, set] = {}
    for u, event in truth.items():
        outlets.setdefault(event, set()).add(outlet.get(u))
    return {u for u, event in truth.items() if len(outlets[event]) >= HEADLINE_OUTLETS}


def score(truth: dict[str, object], found: dict[str, object], outlet: dict[str, str]) -> dict:
    """B-cubed over all articles and over headline-size events."""
    parts = bcubed(truth, found)
    big = headline_articles(truth, outlet)
    return {"all": summary(parts.values()), "headline": summary(pr for u, pr in parts.items() if u in big)}


def difference(a: dict[str, tuple], b: dict[str, tuple], truth: dict[str, object],
               draws: int = 2000, seed: int = 0) -> tuple[float, float, float]:
    """B-cubed F1 of b minus a, with a 95% bootstrap interval: the true events
    resampled `draws` times (articles of one event move together, since their
    scores aren't independent). An interval clear of zero isn't luck.
    a, b: per-article parts (bcubed) of two groupings, over the same articles."""
    events: dict[object, list[str]] = {}
    for u in a:
        if u in b:
            events.setdefault(truth[u], []).append(u)
    stories = list(events.values())

    def f1(sample, parts):
        return summary(parts[u] for story in sample for u in story)["f1"] or 0.0

    base = f1(stories, b) - f1(stories, a)
    rng = random.Random(seed)
    diffs = sorted(f1(s, b) - f1(s, a) for s in ([rng.choice(stories) for _ in stories] for _ in range(draws)))
    return base, diffs[int(0.025 * draws)], diffs[int(0.975 * draws) - 1]
