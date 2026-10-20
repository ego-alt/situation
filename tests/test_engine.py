"""Clustering, places and scoring on the real snapshot of 18 Oct 2026, the
day of the French riots, the Houthi attacks on Saudi airports and the fire
at CFB Wainwright."""
from cluster import cluster
from geo import locate, places
from score import assess, clean_title, is_headline, match_itn, pick_headline
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
    for words in (("macron",), ("wainwright",), ("houthi",)):
        assert any(all(w in h.lower() for w in words) for h in major), words
    assert len(major) == 3                                              # Zelensky's US talks: PRIORITY
    assert not any("crawford" in h.lower() and t != "background" for h, (t, _) in tiers.items())
    assert sum(t != "background" for t, _ in tiers.values()) <= 10          # a high bar
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


def _article(source, position, title="Huge blast hits port city of Odesa", hours=1):
    from datetime import timedelta

    from sources import BY_KEY
    return {"source": source, "outlet": BY_KEY[source].outlet, "title": title, "position": position,
            "published": NOW - timedelta(hours=hours)}


def test_only_front_page_feeds_can_lead():
    """Al Jazeera, DW and France 24 list newest first: their top spot means recent, not lead."""
    newest_first = [_article("aljazeera", 0), _article("dw", 0), _article("france24", 1)]
    a = assess(newest_first, None, None, NOW)
    assert (a["leads"], a["tier"]) == (0, "background")
    a = assess([*newest_first[:2], _article("bbc", 0)], None, None, NOW)
    assert (a["leads"], a["tier"]) == (1, "headline")


def test_in_the_news_makes_flash_only_for_a_day():
    groups, itn = _groups()
    houthi = _find(groups, "houthi")
    item = "Houthi attacks on Saudi airports"
    assert assess(houthi, locate(houthi), item, NOW)["tier"] == "major"
    assert assess(houthi, locate(houthi), item, NOW, itn_fresh=False)["tier"] == "headline"


def test_places_from_leaders_capitals_and_cities():
    assert set(places("Starmer meets Takaichi in Osaka")) == {"GB", "JP"}
    assert places("Lula and Milei meet")["BR"] == places("Lula and Milei meet")["AR"] == 1
    assert "HK" in places("Hong Kong leader John Lee")
    assert places("Trump says")["US"] < 1                              # named as an actor
    # "Georgia" is as often the US state: it counts for less than a clear place.
    assert places("Storm hits Georgia and Atlanta, US")["US"] > places("Storm hits Georgia and Atlanta, US")["GE"]
    assert "FR" not in places("Protests in Limoges")                   # cities from one day's news are gone


# ---------- places inside countries ----------

def _spots(text):
    from geo import spots
    return [(s["name"], s["iso"], s["kind"]) for s in spots(text)]


def test_cities_and_regions_in_the_news():
    assert _spots("Russian forces close in on Pokrovsk as Ukraine evacuates families from Donetsk region") == [
        ("Pokrovsk", "UA", "city"), ("Donetsk", "UA", "region")]
    assert [n for n, _, _ in _spots("France: Paris, Lyon and Marseille count the cost after the riots")] == [
        "Paris", "Lyon", "Marseille"]
    assert ("Saint Petersburg", "RU", "city") in _spots("Drones hit St Petersburg overnight, Russia says")
    assert _spots("Shelling in Kharkiv and Odesa overnight") == [("Kharkiv", "UA", "city"), ("Odesa", "UA", "city")]


def test_names_that_arent_places_here():
    assert _spots("Mbappe scores as Real Madrid win; Manchester City lose in Spain") == []
    assert _spots("Vladimir Putin said Russia would not stop") == []            # a first name
    assert _spots("An investigation by the New York Times, US officials say") == []
    assert _spots("Moscow says talks with Kyiv are over") == [("Kyiv", "UA", "city")]   # Moscow as an actor
    # A town that shares its name counts only where the article names the country.
    assert _spots("Clashes reported near Pokrovsk") == []


def _story(hours_ago_broke, outlets=("bbc", "nyt", "guardian", "aljazeera"), recent=0, title="Earthquake hits city"):
    from datetime import timedelta

    from sources import BY_KEY
    arts = [{"source": o, "outlet": BY_KEY[o].outlet, "title": title, "position": 9,
             "published": NOW - timedelta(hours=hours_ago_broke)} for o in outlets]
    for o in ("france24", "dw", "npr")[:recent]:         # outlets still writing about it
        arts.append({"source": o, "outlet": BY_KEY[o].outlet, "title": title, "position": 9,
                     "published": NOW - timedelta(hours=1)})
    return arts


def test_a_story_fades_from_when_it_broke():
    fresh = assess(_story(2), None, None, NOW)["score"]
    day_old = assess(_story(26), None, None, NOW)["score"]
    assert day_old < fresh * 0.5                          # yesterday's story yields to today's


def test_a_story_still_being_written_about_stays_up():
    quiet = assess(_story(26, recent=0), None, None, NOW)["score"]
    developing = assess(_story(26, recent=3), None, None, NOW)["score"]
    assert developing > quiet * 1.5


def test_sport_and_celebrity_stay_below_the_bar():
    soft = _story(1, title="Ronaldo banned for 30 days over spat")
    assert assess(soft, None, None, NOW)["tier"] == "background"
    assert assess(soft, None, "ITN item", NOW)["tier"] == "background"    # even a recurring final on ITN


def test_wikipedia_confirms_rather_than_decides():
    two = _story(1, outlets=("bbc", "nyt"))
    assert assess(two, None, None, NOW)["tier"] == "background"
    assert assess(two, None, "ITN item", NOW)["tier"] == "headline"         # under-covered: lifted, not FLASH
    four = _story(1)
    assert assess(four, None, "ITN item", NOW)["tier"] == "major"          # well covered and new on ITN
    assert assess(four, None, "ITN item", NOW, itn_fresh=False)["tier"] == "headline"
    bonus = assess(four, None, "ITN item", NOW)["score"] - assess(four, None, None, NOW)["score"]
    assert round(bonus, 1) == 2.0


def test_a_story_is_named_by_its_last_half_day():
    old = _story(30, outlets=("bbc", "nyt", "guardian"), title="Riots spread across France")
    new = _story(2, outlets=("aljazeera", "dw"), title="Curfew imposed in Paris after riots")
    assert assess(old + new, None, None, NOW)["headline"] == "Curfew imposed in Paris after riots"
    assert assess(old, None, None, NOW)["headline"] == "Riots spread across France"   # nothing recent: the story's own


def test_running_story_labels_and_topic_pages_are_not_headlines():
    assert clean_title("France protests escalate with riots – The Latest") == "France protests escalate with riots"
    assert not is_headline("Saudi Arabia")


def test_stories_that_share_names_but_not_events_stay_apart():
    from tests.conftest import SNAPSHOT_3
    items, _ = snapshot_items(SNAPSHOT_3)
    groups = [[items[i]["title"].lower() for i in g] for g in cluster(items)]
    def story(*words):
        return [n for n, g in enumerate(groups) if any(all(w in t for w in words) for t in g)]
    fire, vote = story("fire", "military base"), story("alberta", "referendum")
    assert fire and vote and not set(fire) & set(vote)
    pope = groups[story("pope leo")[0]]
    assert len(pope) >= 8 and all("pope" in t for t in pope)   # his call with Zelenskyy, not the war
    riots = [n for n, g in enumerate(groups) if len(g) >= 10 and sum("riot" in t or "loot" in t for t in g) >= 5]
    assert len(riots) == 1   # the riots, told many ways, are still one story
