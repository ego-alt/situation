"""Stage 1 of grouping: articles into events (a grouping.Layer).

Two articles are one event when they report the same thing. Per pair:

  likeness           word likeness, headline and summary (TF-IDF cosine)
  headline_likeness  the same, headlines alone
  event_likeness     the same, without names: what happened, not who or where
  meaning_likeness   how close their meanings are (embed.py)
  same_country,      their main countries, the same or different
  other_country
  hours_apart        how far apart they went out
  same_outlet, wire  one outlet's follow-ups; a wire's copy

Each article's words are a row of a sparse matrix, so one matrix product
gives every pair's likeness, and features are computed as whole columns.
Candidates are pairs that share words (likeness at least CANDIDATE) or mean
much the same (MEANING_CANDIDATE): about 3% of all pairs in a day's window.
"""
import math
from collections import Counter
from datetime import UTC, datetime

import numpy as np
from scipy import sparse

import embed
import grouping
from cluster import document, names, tfidf, tokens, where

WIRES = {"ap", "reuters"}
CANDIDATE = 0.05
MEANING_CANDIDATE = 0.5


class Articles(grouping.Layer):
    FEATURES = ["likeness", "headline_likeness", "event_likeness", "meaning_likeness",
                "same_country", "other_country", "hours_apart", "same_outlet", "wire"]

    def __init__(self, articles: list[dict], meaning: bool | None = None):
        super().__init__(len(articles))
        self.articles = articles
        self.vectors = tfidf([document(a) for a in articles])
        self.headlines = tfidf([tokens(a["title"]) for a in articles])
        named = self.named = names(articles)
        self.events = [_unit({t: x for t, x in v.items() if t not in named and not t.isdigit()})
                       for v in self.vectors]
        self.country = [main_country(where(a)) for a in articles]
        self.outlet = [a.get("outlet") or a["source"] for a in articles]
        self.time = [(a.get("published") or datetime.now(UTC)).timestamp() for a in articles]
        use = embed.available() if meaning is None else meaning
        self.meaning = embed.encode([embed.text(a) for a in articles]) if use else None
        codes = {c: n for n, c in enumerate({c for c in self.country if c})}
        self._country = np.array([codes.get(c, -1) for c in self.country])
        outlets = {o: n for n, o in enumerate(set(self.outlet))}
        self._outlet = np.array([outlets[o] for o in self.outlet])
        self._wire = np.array([a["source"] in WIRES for a in articles])
        self._time = np.array(self.time)
        self._likeness = {name: similarities(rows) for name, rows in
                          (("words", self.vectors), ("headlines", self.headlines), ("events", self.events))}
        self._candidates = None

    def candidates(self) -> list[tuple[int, int]]:
        if self._candidates is None:
            words = sparse.triu(self._likeness["words"], k=1).tocoo()
            keep = words.data >= CANDIDATE
            found = set(zip(words.row[keep].tolist(), words.col[keep].tolist(), strict=True))
            if self.meaning is not None and self.n > 1:
                i, j = np.nonzero(np.triu(self.meaning @ self.meaning.T, 1) >= MEANING_CANDIDATE)
                found |= set(zip(i.tolist(), j.tolist(), strict=True))
            self._candidates = sorted(found)
        return self._candidates

    def pair_rows(self, i: np.ndarray, j: np.ndarray) -> np.ndarray:
        ci, cj = self._country[i], self._country[j]
        return np.column_stack([
            grouping.pick(self._likeness["words"], i, j),
            grouping.pick(self._likeness["headlines"], i, j),
            grouping.pick(self._likeness["events"], i, j),
            np.einsum("ij,ij->i", self.meaning[i], self.meaning[j]) if self.meaning is not None else np.zeros(len(i)),
            ((ci >= 0) & (ci == cj)).astype(float),
            ((ci >= 0) & (cj >= 0) & (ci != cj)).astype(float),
            np.log1p(np.abs(self._time[i] - self._time[j]) / 3600),
            (self._outlet[i] == self._outlet[j]).astype(float),
            (self._wire[i] | self._wire[j]).astype(float),
        ])


def main_country(found: Counter) -> str | None:
    """The country named most, when it has at least half the mentions."""
    if not found:
        return None
    iso, n = found.most_common(1)[0]
    return iso if n >= 0.5 * sum(found.values()) else None


def similarities(rows: list[dict]):
    """Every pair's cosine likeness, from unit-length sparse rows: one product."""
    vocab: dict[str, int] = {}
    data, col, ptr = [], [], [0]
    for v in rows:
        for t, x in v.items():
            col.append(vocab.setdefault(t, len(vocab)))
            data.append(x)
        ptr.append(len(col))
    m = sparse.csr_matrix((data, col, ptr), shape=(len(rows), max(len(vocab), 1)))
    return (m @ m.T).tocsr()


def _unit(v: dict) -> dict:
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {t: x / norm for t, x in v.items()}


def fit(examples: list[tuple[list[float], bool]], meaning: bool, first: dict | None = None,
        clustering: str = "average") -> dict | None:
    """A stage-1 model (see grouping.fit); without meaning likeness when the
    embedding model isn't here."""
    return grouping.fit(Articles, examples, first=first, clustering=clustering, embed=meaning,
                        leave_out=() if meaning else ("meaning_likeness",))
