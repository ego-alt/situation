import pytest

import embed
import learn
from news import evidence_names, news_only

ITEMS = [{"source": "bbc", "title": "Opinion: The protests are a warning", "url": "https://example.com/world/1",
          "summary": ""},
         {"source": "bbc", "title": "Parliament passes the budget after a late vote", "url": "https://example.com/world/2",
          "summary": "Lawmakers approved the budget on Tuesday."}]


def _model(**changes) -> dict:
    """A model that only looks at the headline rule."""
    names = evidence_names()
    weights = [8.0 if n == "headline" else 0.0 for n in names] + [0.0] * 64 + [0.0]
    return {"evidence": names, "tokens": ["outlet:bbc"], "mean": [0.0] * 256, "components": [[0.0] * 256] * 64,
            "weights": weights, "bias": -4.0, "threshold": 0.5} | changes


def test_without_a_model_the_rules_decide():
    with learn.using({}):
        assert [a["title"] for a in news_only(ITEMS)] == [ITEMS[1]["title"]]


def test_a_model_saved_for_other_evidence_is_not_used():
    with learn.using({"news": _model(evidence=["headline", "url"], threshold=0.0)}):
        assert news_only(ITEMS) == [ITEMS[1]]            # the rules, not a model that would drop everything


@pytest.mark.skipif(not embed.available(), reason="needs the embedding model")
def test_the_model_drops_what_it_is_sure_isnt_news():
    with learn.using({"news": _model()}):
        assert news_only(ITEMS) == [ITEMS[1]]
    with learn.using({"news": _model(threshold=0.0)}):
        assert news_only(ITEMS) == []                    # everything scores above a zero threshold
