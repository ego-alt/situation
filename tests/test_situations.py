"""Running stories (situations.py): a burst of words across many outlets and
events becomes one story, named for what its headlines call it; a quiet day,
a single big event or too little history finds none."""
from datetime import UTC, datetime, timedelta

import situations
from news import is_news, results_page

NOW = datetime(2026, 11, 4, 3, tzinfo=UTC)
OUTLETS = ["ap", "reuters", "bbc", "guardian", "nyt", "aljazeera", "dw", "npr"]


def _article(title: str, outlet: str, hours_ago: float = 2, summary: str = "") -> dict:
    return {"id": hash((title, outlet)), "source": outlet, "outlet": outlet, "title": title, "summary": summary,
            "url": f"https://{outlet}.example/{abs(hash(title))}", "published": NOW - timedelta(hours=hours_ago)}


def _fortnight(extra: list[str] = ()) -> list[dict]:
    """Fourteen ordinary days: a few stories a day, none about the midterms."""
    titles = ["Parliament passes the budget after a late vote", "Floods force thousands from their homes",
              "Central bank holds interest rates steady", "Talks on the trade deal resume in Geneva", *extra]
    return [_article(t, OUTLETS[i % len(OUTLETS)], hours_ago=24 + 24 * d + i)
            for d in range(14) for i, t in enumerate(titles)]


# Election night: race calls and voting news, each worded its own way.
EVENTS = [
    ["Democrat wins North Carolina Senate race in midterm elections",
     "North Carolina Senate race won by Democrat as midterm results come in",
     "Democrats flip North Carolina Senate seat in the midterm elections"],
    ["Republican wins Florida governor race as midterm results arrive",
     "Florida governor race goes to the Republican in the midterm elections"],
    ["Polls close across the eastern states in the midterm elections",
     "Midterm elections: polls close in the eastern states as counting begins"],
    ["Ballot shortages reported in Michigan during the midterm elections"],
    ["Democrats need two seats to win control of the House in the midterm elections"],
]


def _night():
    groups, k = [], 0
    for titles in EVENTS:
        group = []
        for t in titles:
            group.append(_article(t, OUTLETS[k % len(OUTLETS)]))
            k += 1
        groups.append(group)
    groups.append([_article("Rail strike halts trains across the country", "bbc"),
                   _article("Train drivers strike over pay, halting services", "dw")])
    day = [a for g in groups for a in g]
    # the wider coverage that night: more outlets writing about the midterms
    day += [_article(f"Midterm elections: Democrats and Republicans await results in race {n}", OUTLETS[n % 8])
            for n in range(12)]
    return groups, day


def test_an_election_night_is_one_running_story():
    groups, day = _night()
    usual, days = situations.baseline(_fortnight())
    found = situations.find(groups, situations.topics(day, usual, days))
    assert len(found) == 1
    story = found[0]
    assert "midterm" in story["name"].lower()
    assert sorted(story["events"]) == [0, 1, 2, 3, 4]           # the rail strike stays out
    assert "midterm" in story["words"]


def test_a_word_that_is_always_in_the_news_does_not_burst():
    groups, day = _night()
    usual, days = situations.baseline(_fortnight(extra=["Midterm elections campaign enters its final week"] * 10))
    # "midterm" was already everywhere: it's no longer one of the story's words
    assert all("midterm" not in t["words"] for t in situations.topics(day, usual, days))


def test_too_little_history_finds_nothing():
    groups, day = _night()
    usual, days = situations.baseline([a for a in _fortnight() if a["published"] > NOW - timedelta(days=4)])
    assert days < situations.MIN_BASELINE_DAYS
    assert situations.topics(day, usual, days) == []


def test_one_big_event_is_not_a_running_story():
    groups, day = _night()
    usual, days = situations.baseline(_fortnight())
    # only the race call is shared by two outlets: the rest are single reports
    single = [groups[0]] + [[g[0]] for g in groups[1:5]]
    assert situations.find(single, situations.topics(day, usual, days)) == []


def test_one_big_event_with_small_ones_sharing_its_words_is_that_event():
    """A race call in twenty outlets beside a few small events is the race call, already a
    story (as Joshua Wong's sentencing was beside other court cases, 6 Nov 2026)."""
    groups, day = _night()
    usual, days = situations.baseline(_fortnight())
    big = [_article(f"{EVENTS[0][n % 3]} ({n})", OUTLETS[n % len(OUTLETS)]) for n in range(20)]
    assert situations.find([big, *groups[1:5]], situations.topics(day + big, usual, days)) == []


def test_a_running_story_is_named_as_its_headlines_write_it():
    titles = ["Hong Kong activist Joshua Wong sentenced to 10 years", "Joshua Wong jailed for 10 years",
              "Hong Kong Sentences Activist Joshua Wong to 10 Years in Prison"]
    assert "Joshua Wong" in situations._name(titles, "wong", "HK")


def test_results_pages_are_not_news():
    for title in ["Ohio Governor Election Live Results 2026", "2026 Kentucky Midterm Election Results",
                  "2026 U.S. House Election Results"]:
        assert results_page({"title": title}) and not is_news({"title": title, "url": "https://ap.example/x"})
    for title in ["Election results show Democrats win the House", "Democrat Ossoff wins Georgia Senate race"]:
        assert not results_page({"title": title})


def test_an_event_mostly_about_another_country_goes_to_its_own():
    """Naming the US in passing doesn't make South Africa's election part of the midterms;
    an event that names the US at least half as often as anything stays."""
    from collections import Counter
    assert situations._home(Counter({"ZA": 17, "US": 3}), "US") == "ZA"
    assert situations._home(Counter({"IL": 4, "US": 3}), "US") == "US"
    assert situations._home(Counter(), "US") == "US"
