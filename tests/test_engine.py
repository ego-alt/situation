"""Clustering, places and scoring on the real snapshot of 18 Oct 2026, the
day of the French riots, the Houthi attacks on Saudi airports and the fire
at CFB Wainwright."""
from cluster import cluster
from geo import locate, places
from score import assess, clean_title, match_itn, pick_headline
from tests.conftest import NOW, snapshot_items


def _groups():
    items, itn = snapshot_items()
    return [[items[i] for i in g] for g in cluster(items)], itn


def _find(groups, *words):
    for g in groups:
        text = " ".join(a["title"] for a in g).lower()
        if all(w in text for w in words):
            return g
    raise AssertionError(f"no group with {words}")


def test_the_french_riots_are_one_story_across_many_outlets():
    groups, _ = _groups()
    france = _find(groups, "macron", "violence")
    assert len({a["outlet"] for a in france}) >= 9


def test_local_holiday_chatter_stays_off_the_screen():
    """Two Hong Kong outlets on the Chung Yeung weekend group together, but a
    holiday rush is not news you need."""
    groups, itn = _groups()
    holiday = _find(groups, "huanggang")
    assert assess(holiday, locate(holiday), None, NOW)["tier"] == "background"


def test_tiers_on_the_day():
    groups, itn = _groups()
    matched = match_itn(groups, itn["items"])
    tiers = {}
    for k, g in enumerate(groups):
        a = assess(g, locate(g), matched.get(k), NOW)
        tiers[a["headline"]] = (a["tier"], a["itn"])
    major = [h for h, (t, _) in tiers.items() if t == "major"]
    for words in (("macron",), ("wainwright",), ("houthi",), ("zelensky",)):
        assert any(all(w in h.lower() for w in words) for h in major), words
    assert not any("crawford" in h.lower() and t != "background" for h, (t, _) in tiers.items())
    assert sum(t != "background" for t, _ in tiers.values()) <= 15          # a high bar
    itn_hits = [h for h, (_, i) in tiers.items() if i]
    assert any("macron" in h.lower() for h in itn_hits) and any("houthi" in h.lower() for h in itn_hits)


def test_places():
    assert places("The Pope met officials in The Hague")["NL"] == 1
    assert "NL" not in places("The fire spread")                      # "The" is not a place
    assert set(places("South Sudan and Sudan")) == {"SS", "SD"}
    assert "HK" in places("Hong Kong's leader John Lee")
    assert places("Washington says")["US"] < 1                        # named as an actor


def test_headline_skips_live_blogs_and_emoji():
    arts = [{"title": "🔴 Watch live: Macron addresses the nation"}, {"title": "Macron condemns violence"}]
    assert pick_headline(arts) == "Macron condemns violence"
    assert clean_title("🔴Live: Paris braces") == "Paris braces"
