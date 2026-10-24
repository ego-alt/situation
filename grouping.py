"""One layer of grouping, written once and configured per layer.

Articles group into events (events.py), events into situations
(situations.py). Each is the same machine over different units:

  1. candidates  which pairs of units are worth scoring at all (most pairs
                 obviously aren't): the layer decides
  2. features    a row of numbers per pair: the layer's own, then optionally
                 three neighbourhood ones (below)
  3. score       a logistic regression turns each row into the chance the pair
                 is one group (learn.fit_logistic, trained on labels)
  4. bundle      units linked by a probability of at least LINK form bundles;
                 groups never cross bundles (every pair between them is too
                 unlikely to tip a merge), so each is merged on its own
  5. merge       greedy agglomeration, best first, judged on all the pairs
                 between two groups, never one pair alone (so one article
                 about two events can't glue them together):
                   correlation  join while the summed evidence (log-odds) of
                                all their pairs says "same"; a pair never
                                scored counts as UNSCORED
                   average      join while their pairs average at least MERGE
  6. order       each group's units in the layer's order (its most typical
                 first: its headline names the group)

Neighbourhood features, in a second pass: the first pass's probabilities make
a graph of confident links (at least STRONG); for each pair, how many
neighbours the two share and what share of their neighbours that is (entry
(i, j) of A x A, A the graph's adjacency matrix). Two units both strongly
linked to the same crowd are one group however little they share themselves:
the main idea of a graph network, in two cheap passes.
"""
import heapq
import math

import numpy as np
from scipy import sparse

import learn

NEIGHBOUR_FEATURES = ["first_pass", "shared_neighbours", "neighbour_overlap"]
STRONG = 0.5
LINK = 0.2
MERGE = 0.5
UNSCORED = 0.05


class Layer:
    """A set of units to group. A layer gives FEATURES, candidates() and
    pair_rows(); everything else is shared."""

    FEATURES: list[str] = []
    MERGE_RULE = "average"           # when a model doesn't say

    def __init__(self, n: int):
        self.n = n
        self._first = None

    # What a layer provides -------------------------------------------------

    def candidates(self) -> list[tuple[int, int]]:
        """Pairs (i < j) worth scoring."""
        raise NotImplementedError

    def pair_rows(self, i: np.ndarray, j: np.ndarray) -> np.ndarray:
        """The layer's own FEATURES for pairs (i[k], j[k]), one row each."""
        raise NotImplementedError

    def ordered(self, group: list[int], near: dict[int, dict[int, float]]) -> list[int]:
        """A group's units, most typical first: the one most likely to belong
        with the rest."""
        fit = {u: sum(near[u].get(v, 0.0) for v in group) for u in group}
        return sorted(group, key=lambda u: -fit[u])

    # Shared ------------------------------------------------------------------

    @classmethod
    def all_features(cls) -> list[str]:
        return cls.FEATURES + NEIGHBOUR_FEATURES

    def rows(self, pairs: list[tuple[int, int]], first: dict | None = None) -> np.ndarray:
        """Every feature (all_features) of each pair; the neighbourhood ones from
        the first-pass model when there is one, else zeros."""
        if pairs:
            i, j = (np.array(x, dtype=int) for x in zip(*pairs, strict=True))
        else:
            i = j = np.array([], dtype=int)
        own = self.pair_rows(i, j).reshape(len(i), len(self.FEATURES))
        extra = self.neighbourhood(first, i, j) if first else np.zeros((len(i), len(NEIGHBOUR_FEATURES)))
        return np.hstack([own, extra])

    def probabilities(self, model: dict, pairs) -> dict[tuple[int, int], float]:
        pairs = list(pairs)
        if not pairs:
            return {}
        rows = self.rows(pairs, model.get("first"))
        columns = [self.all_features().index(f) for f in model["features"]]
        return dict(zip(pairs, learn.predict_logistic(model, rows[:, columns]).tolist(), strict=True))

    def neighbourhood(self, first: dict, i: np.ndarray, j: np.ndarray) -> np.ndarray:
        """For each pair: the first pass's log-odds that it's one group, how many
        strong neighbours the two share, and what share of their neighbours that is."""
        if self._first is None or self._first[0] is not first:
            p1 = self.probabilities(first, self.candidates())
            strong = [(a, b) for (a, b), q in p1.items() if q >= STRONG]
            rows = [a for a, _ in strong] + [b for _, b in strong]
            cols = [b for _, b in strong] + [a for a, _ in strong]
            adjacency = sparse.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(self.n, self.n))
            self._first = (first, p1, adjacency, (adjacency @ adjacency).tocsr(),
                           np.asarray(adjacency.sum(axis=1)).ravel())
        _, p1, adjacency, shared_all, degree = self._first
        pairs = list(zip(i.tolist(), j.tolist(), strict=True))
        known = [p1.get(pair) for pair in pairs]
        missing = [pair for pair, q in zip(pairs, known, strict=True) if q is None]
        if missing:
            extra = self.probabilities(first, missing)
            known = [q if q is not None else extra[pair] for pair, q in zip(pairs, known, strict=True)]
        q = np.clip(np.array(known, dtype=float), 1e-4, 1 - 1e-4)
        linked = pick(adjacency, i, j)
        shared = pick(shared_all, i, j)
        union = degree[i] + degree[j] - 2 * linked - shared
        return np.column_stack([np.log(q / (1 - q)), np.log1p(shared),
                                np.divide(shared, union, out=np.zeros_like(shared), where=union > 0)])

    def group(self, model: dict) -> list[list[int]]:
        """The units grouped by the model: biggest group first, each in the
        layer's order."""
        p = self.probabilities(model, self.candidates())
        near: dict[int, dict[int, float]] = {u: {} for u in range(self.n)}
        parent = list(range(self.n))

        def find(u):
            while parent[u] != u:
                parent[u] = parent[parent[u]]
                u = parent[u]
            return u
        for (a, b), q in p.items():
            near[a][b] = near[b][a] = q
            if q >= LINK:
                parent[find(a)] = find(b)
        bundles: dict[int, list[int]] = {}
        for u in range(self.n):
            bundles.setdefault(find(u), []).append(u)
        rule = model.get("clustering", self.MERGE_RULE)
        groups = []
        for members in bundles.values():
            found = correlation(members, near) if rule == "correlation" else average_linkage(members, near)
            groups += [self.ordered(g, near) for g in found]
        return sorted(groups, key=len, reverse=True)


def pick(matrix, i: np.ndarray, j: np.ndarray) -> np.ndarray:
    """matrix[i[k], j[k]] for every k, as a flat array."""
    if len(i) == 0:
        return np.zeros(0)
    return np.asarray(matrix[i, j]).ravel().astype(float)


# Merging -----------------------------------------------------------------------

def average_linkage(members: list[int], near: dict[int, dict[int, float]]) -> list[list[int]]:
    """Join the two groups whose pairs are most alike on average, while that
    average is at least MERGE; pairs never scored count as 0."""
    return agglomerate(members, near, lambda q: q, lambda total, n: total / n, MERGE)


def correlation(members: list[int], near: dict[int, dict[int, float]]) -> list[list[int]]:
    """Join the two groups whose pairs, all together, most favour "same": each
    pair adds its log-odds, a pair never scored counts as UNSCORED. Joining
    stops when no two groups' evidence comes out in favour. This is greedy
    additive edge contraction (GAEC), the standard heuristic for correlation
    clustering. (A Kernighan-Lin pass of local moves after it was tried in
    October 2026 and gained nothing on the labels.)"""
    prior = math.log(UNSCORED / (1 - UNSCORED))
    return agglomerate(members, near, _odds, lambda total, n: total + n * prior, 1e-9)


def _odds(q: float) -> float:
    """A pair's evidence for "same", relative to a pair never scored."""
    q = min(max(q, 1e-4), 1 - 1e-4)
    return math.log(q / (1 - q)) - math.log(UNSCORED / (1 - UNSCORED))


def agglomerate(members, near, weight, value, threshold) -> list[list[int]]:
    """Merge groups best-first while value(total weight between them, number of
    pairs between them) >= threshold. A heap holds the candidate joins; an entry
    gone stale (a group since grown) is skipped."""
    parts = {u: [u] for u in members}
    link = {u: {v: weight(q) for v, q in near[u].items() if v in parts} for u in members}
    between = lambda a, c: len(parts[a]) * len(parts[c])  # noqa: E731
    heap = [(-value(t, 1), a, c) for a in members for c, t in link[a].items() if a < c and value(t, 1) >= threshold]
    heapq.heapify(heap)
    while heap:
        neg, a, c = heapq.heappop(heap)
        if a not in parts or c not in parts or c not in link[a]:
            continue
        if abs(-neg - value(link[a][c], between(a, c))) > 1e-9:
            continue                                     # stale: the groups changed since
        parts[a] += parts.pop(c)
        for d, total in link.pop(c).items():
            if d == a:
                continue
            del link[d][c]
            link[a][d] = link[d][a] = link[a].get(d, 0.0) + total
        link[a].pop(c, None)
        for d, total in link[a].items():
            v = value(total, between(a, d))
            if v >= threshold:
                heapq.heappush(heap, (-v, min(a, d), max(a, d)))
    return list(parts.values())


# Training --------------------------------------------------------------------------

def fit(layer: type[Layer], examples: list[tuple[list[float], bool]], *, first: dict | None = None,
        clustering: str | None = None, leave_out: tuple[str, ...] = (), **extra) -> dict | None:
    """A model for a layer, from (all_features row, same group) examples: its
    own features (less leave_out), plus the neighbourhood ones with a
    first-pass model (whose probabilities the examples must carry)."""
    if len({y for _, y in examples}) < 2:
        return None
    features = [f for f in layer.FEATURES if f not in leave_out] + (NEIGHBOUR_FEATURES if first else [])
    columns = [layer.all_features().index(f) for f in features]
    x = np.array([f for f, _ in examples])[:, columns]
    y = np.array([float(s) for _, s in examples])
    return {"features": features, **({"clustering": clustering} if clustering else {}),
            **({"first": first} if first else {}), **extra, **learn.fit_logistic(x, y)}
