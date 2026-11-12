"""Dispatch: what an issue is made from (the journals' papers, the features, the climbing calendar,
the prints, the poems, the holidays), how it's made up each day and how it's kept. The journals are
stood in for by records shaped like Europe PMC's, the feeds and the calendar by ones shaped like theirs."""
import re
from datetime import UTC, date, datetime, timedelta

import pytest
import requests

import climbing
import dispatch
import features
import papers
from models import Issue, Paper, SourceStatus, db
from pipeline import record_sources

LONG = "Neurons in the cortex " + "and their synapses " * 30     # an abstract, not front matter
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)                     # the day's issue is due


def record(doi, title, types=("Journal Article",), abstract=LONG, first="2026-10-28", online=None, issn="0896-6273",
           comments=(), authors=("Ada", "Ben", "Cy"), pmcid=None, created=None, printed="2026 Oct 28"):
    return {"doi": doi, "title": title, "abstractText": abstract, "firstPublicationDate": first,
            "electronicPublicationDate": online, "dateOfCreation": created, "pmcid": pmcid,
            "pubTypeList": {"pubType": list(types)},
            "journalInfo": {"journal": {"issn": issn}, "dateOfPublication": printed},
            "authorList": {"author": [{"lastName": a} for a in authors]},
            "commentCorrectionList": {"commentCorrection": [{"type": t, "reference": f"Journal. doi: {d}."}
                                                            for t, d in comments]}}


NEURON = [
    record("10.1016/j.neuron.1", "A circuit for <i>thirst</i>.", online="2026-10-27", pmcid="PMC1"),
    record("10.1016/j.neuron.old", "An older paper on memory", online="2026-08-06"),          # outside the window
    # A Preview: dated only by its print month; its paper is found through its references.
    record("10.1016/j.neuron.p1", "Running in reverse", types=("Comment", "Journal Article"), abstract="",
           first="2026-10-01", created="2026-10-28", printed="2026 Oct"),
    record("10.1016/j.neuron.r1", "A review of reviews", types=("Review", "Journal Article")),
]
SCIENCE = [
    record("10.1126/science.brain", "Ultrarapid decision-making in an avian brain", first="2026-10-29",
           issn="0036-8075", comments=[("Comment in", "10.1126/science.persp")]),
    record("10.1126/science.rock", "Western Pacific double subduction", first="2026-10-29", issn="0036-8075",
           abstract="Plate tectonics " * 40),
    record("10.1126/science.persp", "The cognitive power of the avian brain", types=("Comment", "Journal Article"),
           abstract="", first="2026-10-29", issn="0036-8075", comments=[("Comment on", "10.1126/science.brain")]),
    record("10.1126/science.news", "Fired biologist wins lawsuit", types=("News",), first="2026-10-29",
           issn="0036-8075"),
]
NATURE = [
    record("10.1038/s41586-1", "Perinatal elimination of aberrant cortical neurons", issn="0028-0836"),
    record("10.1038/s41586-2", "Emotion regulation lacks a shared neural signature", issn="0028-0836"),
    record("10.1038/s41586-3", "Eukaryotes' closest relatives", issn="0028-0836"),            # not neuroscience
    record("10.1038/d41586-nv", "Meta-study sparks questions", types=("News",), abstract="", issn="0028-0836"),
    record("10.1038/d41586-news", "The brain liquidates mutant neurons", types=("News",), abstract="",
           issn="0028-0836"),
]
BY_ISSN = {"0896-6273": NEURON, "0036-8075": SCIENCE, "0028-0836": NATURE}
# The Preview's first reference is another journal's paper: a commentary is on one of its own journal's.
REFERENCES = {"10.1016/j.neuron.p1": ["10.1126/science.brain", "10.1016/j.neuron.old", "10.1002/elsewhere"],
              "10.1038/d41586-nv": ["10.1038/s41586-2"]}


@pytest.fixture
def journals(monkeypatch):
    """The journals, the publishers' pages and Crossref, all stood in for; no features, no climbing."""
    monkeypatch.setattr(papers, "europe_pmc", lambda issn, since, until: [dict(r) for r in BY_ISSN.get(issn, [])])
    monkeypatch.setattr(features, "take", lambda now, carried: [])
    monkeypatch.setattr(climbing, "ahead", lambda now: [])

    class Found:
        def __init__(self, results):
            self.results = results

        def json(self):
            return {"resultList": {"result": self.results}}

    def get(url, **params):   # only _adopt asks Europe PMC for one DOI
        doi = params["query"].split('"')[1]
        return Found([r for rs in BY_ISSN.values() for r in rs if r["doi"] == doi])

    monkeypatch.setattr(papers, "get", get)
    monkeypatch.setattr(papers, "subject_dois", lambda url: {"10.1038/s41586-1", "10.1038/s41586-2",
                                                             "10.1038/d41586-nv", "10.1038/d41586-news"})
    monkeypatch.setattr(papers, "page_meta",
                        lambda url: {"dc.type": "News And Views" if url.endswith("-nv") else "News",
                                     "description": "What the meta-study means."})
    monkeypatch.setattr(papers, "references", lambda doi: REFERENCES.get(doi, []))
    monkeypatch.setattr(papers, "figure_for", lambda doi: f"https://img.example/{doi}.png")
    monkeypatch.setattr(papers, "cover_story", lambda url: ("10.1038/s41586-1", "https://img.example/cover.jpg"))


# ---------- the papers ----------

def test_clean_keeps_only_italics_and_scripts():
    assert (papers.clean("&lt;i&gt;Corvus&lt;/i&gt; <b>corone</b> & Ca<sup>2+</sup>")
            == "<i>Corvus</i> corone &amp; Ca<sup>2+</sup>")
    assert papers.clean('<script>x</script><a href="javascript:1">y</a>') == "x y"
    # Reference numbers go (their list isn't shown); a power of ten stays.
    assert papers.clean("well-being<sup>1</sup> and circuits<sup>3–5</sup>, 10<sup>6</sup> cells") == \
        "well-being and circuits, 10<sup>6</sup> cells"


def test_paragraphs_are_parted_and_links_dissolve():
    link = '<a href="https://example.org"><span>budget</span><svg><path d="m1"/></svg></a>'
    parts = [f"<span>The </span>{link}<span>, in <em>full</em>.</span>", "One line<br/>and the next", "", None]
    assert papers.paragraphs(parts) == "The budget, in <i>full</i>.\n\nOne line\nand the next"


def test_fetch_stores_the_papers_in_scope_and_ties_commentary(app, journals):
    papers.fetch(NOW)
    research = {p.doi for p in Paper.query.filter_by(kind="research")}
    assert research == {"10.1016/j.neuron.1", "10.1016/j.neuron.old", "10.1126/science.brain",
                        "10.1038/s41586-1", "10.1038/s41586-2"}
    on = {p.doi: p.on for p in Paper.query.filter_by(kind="commentary")}
    assert on == {"10.1016/j.neuron.p1": "10.1016/j.neuron.old",       # through its references, paper added
                  "10.1126/science.persp": "10.1126/science.brain",    # PubMed's own link
                  "10.1038/d41586-nv": "10.1038/s41586-2"}             # News & Views, told apart from news
    assert db.session.get(Paper, "10.1038/d41586-news").kind == "other"
    first = db.session.get(Paper, "10.1016/j.neuron.1")
    assert first.title == "A circuit for <i>thirst</i>" and first.authors == ["Ada", "Ben", "Cy"]
    assert first.free["label"] == "PubMed Central" and not first.figure                # no commentary: it won't run
    assert db.session.get(Paper, "10.1126/science.brain").figure == "https://img.example/10.1126/science.brain.png"
    assert db.session.get(Paper, "10.1038/s41586-1").cover == "https://img.example/cover.jpg"


def test_the_papers_waiting_are_spread_over_the_days_and_each_runs_once(app, journals):
    """Waiting are the cover story and the papers with a commentary (not neuron.1, which has none):
    the cover story and the longest out first, a third of them a day."""
    ran = []
    for d in range(4):
        assert dispatch.publish(NOW + timedelta(days=d)) == (NOW + timedelta(days=d)).date().isoformat()
        ran.append(dispatch.issue(None)["stories"])
    assert [[s["id"] for s in stories] for stories in ran] == [
        ["10.1038/s41586-1", "10.1016/j.neuron.old"], ["10.1038/s41586-2"], ["10.1126/science.brain"], []]
    cover, late = ran[0]
    assert cover["cover"] and cover["cover_image"] == "https://img.example/cover.jpg" and cover["source"] == "Nature"
    assert not cover["earlier"]                                                 # a week old, no more
    assert late["earlier"] and late["commentary"][0]["label"] == "Preview"     # from August: its Preview is new
    [note] = ran[1][0]["commentary"]
    assert (note["label"], note["summary"]) == ("News & Views", "What the meta-study means.")   # from its page
    assert db.session.get(Paper, "10.1126/science.brain").ran == "2026-11-06"


# ---------- the features ----------

EARTH_FEED = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"
 xmlns:content="http://purl.org/rss/1.0/modules/content/"xmlns:media="http://search.yahoo.com/mrss/" >
<channel><title>NASA Science</title>{}</channel></rss>"""
EARTH_ITEM = """<item><title>{title}</title><link>https://science.nasa.gov/earth/earth-observatory/{slug}/</link>
<pubDate>{day} 05:01:00 +0000</pubDate><category><![CDATA[{category}]]></category>
<content:encoded><![CDATA[<nav><p>Menu</p></nav>
<figure><img src="https://assets.science.nasa.gov/{slug}.jpg?w=1024&#038;fit=clip" alt=""/></figure>
<p class="wp-block-paragraph">Blanket bogs store <a href="https://example.org">vast amounts</a> of carbon.</p>
<p class="wp-block-paragraph">The image shows the <em>Flow Country</em>.</p>
<p class="wp-block-paragraph"><em>NASA Earth Observatory image by A. Mapper. Story by Kathryn Hansen.</em></p>
<h2 class="wp-block-heading">Downloads</h2><p class="wp-block-paragraph">JPEG (4 MB)</p>]]></content:encoded></item>"""
OWID_FEED = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<title>Data Insights</title><entry><title><![CDATA[How much CO2 can the world emit?]]></title>
<id>https://ourworldindata.org/data-insights/co2</id><published>2026-11-03T00:00:00.000Z</published>
<link rel="alternate" href="https://ourworldindata.org/data-insights/co2"/>
<author><name>Hannah Ritchie</name></author><author><name>Pablo Rosado</name></author>
<content type="html"><![CDATA[
<img src="https://ourworldindata.org/cdn-cgi/imagedelivery/x/abc/w=1350" alt="Bar chart" width="1350" height="1350"/>
<p class="article-block__text"><span class="">Carbon budgets (CO</span><sub><span class="">2</span></sub><span
 class="">) shrink.</span></p>
<ul><li><span class="">The 1.5°C budget is 3.5 years of emissions.</span></li></ul>
<p class="article-block__text"><a href="https://ourworldindata.org/co2"><span class="">Explore more →</span></a></p>
]]></content></entry></feed>"""


@pytest.fixture
def feeds(monkeypatch):
    """The two features' feeds: Earth Observatory with a stale piece, two fresh ones and one that isn't its own."""
    earth = EARTH_FEED.format("".join(EARTH_ITEM.format(title=t, slug=s, day=d, category=c) for t, s, d, c in (
        ("A solar flare", "flare", "Tue, 03 Nov 2026", "Sun"),
        ("Tuesday’s image", "tue", "Tue, 03 Nov 2026", "Earth Observatory"),
        ("Monday’s image", "mon", "Mon, 02 Nov 2026", "Earth Observatory"),
        ("Last week’s image", "old", "Wed, 28 Oct 2026", "Earth Observatory"))))

    class Page:
        def __init__(self, text):
            self.content = text.encode()

    monkeypatch.setattr(features, "get", lambda url: Page(earth if "nasa" in url else OWID_FEED))


def test_a_feature_is_read_whole_from_its_feed(feeds):
    earth, owid = (features.pieces(f) for f in features.FEATURES)
    assert [s["title"] for s in earth] == ["Last week’s image", "Monday’s image", "Tuesday’s image"]   # oldest first
    s = earth[1]
    assert s["abstract"] == "Blanket bogs store vast amounts of carbon.\n\nThe image shows the <i>Flow Country</i>."
    assert (s["source"], s["authors"], s["published"]) == ("NASA Earth Observatory", ["Kathryn Hansen"], "2026-11-02")
    assert s["id"] == s["url"] == "https://science.nasa.gov/earth/earth-observatory/mon/"
    assert (s["figure"], s["figure_large"]) == ("https://assets.science.nasa.gov/mon.jpg?w=640&fit=clip",
                                                "https://assets.science.nasa.gov/mon.jpg?w=1280&fit=clip")
    [chart] = owid
    assert chart["abstract"] == "Carbon budgets (CO<sub>2</sub>) shrink.\n\nThe 1.5°C budget is 3.5 years of emissions."
    assert chart["authors"] == ["Hannah Ritchie", "Pablo Rosado"]
    assert chart["figure"] == "https://ourworldindata.org/cdn-cgi/imagedelivery/x/abc/w=675"


def test_an_issue_takes_each_features_oldest_fresh_piece_once(app, feeds):
    first = features.take(NOW, set())
    assert [s["title"] for s in first] == ["Monday’s image", "How much CO2 can the world emit?"]   # not last week's
    second = features.take(NOW, {s["id"] for s in first})
    assert [s["title"] for s in second] == ["Tuesday’s image"]
    assert db.session.get(SourceStatus, "features:earth").items == 3


def test_a_feed_that_doesnt_answer_is_only_late(app, monkeypatch):
    def down(url):
        raise requests.ConnectionError

    for module in (features, climbing):
        monkeypatch.setattr(module, "get", down)
    monkeypatch.setattr(papers, "fetch", lambda now: record_sources({"papers:nature": (1, None)}, now))
    assert dispatch.publish(NOW) == "2026-11-04"
    shown = dispatch.issue(None)
    assert (shown["stories"], shown["climbing"]) == ([], []) and shown["print"] and shown["poem"]
    assert shown["sources"]["failing"] == ["NASA Earth Observatory", "Our World in Data", "the climbing calendar"]


# ---------- the climbing ----------

def climb(name, kind, starts, disciplines=("boulder",)):
    return {"name": name, "kind": kind, "disciplines": list(disciplines), "starts_at": f"{starts}:00+02:00",
            "ends_at": f"{starts[:10]}T22:00:00+02:00", "stream_url": "https://youtu.be/x"}


CALENDAR = {"events": [
    {"league_name": climbing.LEAGUE, "location": "Innsbruck", "country_name": "Austria", "rounds": [
        climb("Men's Boulder Final", "final", "2026-06-16T19:30"),                              # days ago
        climb("Women's Boulder Qualification", "qualification", "2026-06-18T09:00"),
        climb("Women's Boulder Final", "final", "2026-06-18T19:30"),                            # last night's: a replay
        climb("Men's Speed Final", "final", "2026-06-19T19:30", ("speed",)),
        climb("Men's & Women's Lead Semi-Final", "semi-final", "2026-06-20T19:35", ("lead",)),
        climb("Men's & Women's Lead Final", "final", "2026-06-28T19:35", ("lead",))]},          # past the week ahead
    {"league_name": "IFSC Paraclimbing", "location": "Innsbruck", "country_name": "Austria", "rounds": [
        climb("Men's & Women's Lead Final", "final", "2026-06-19T16:00", ("lead",))]},
]}


def test_the_climbing_is_the_world_cups_boulder_and_lead_semis_and_finals():
    found = climbing.rounds(CALENDAR, datetime(2026, 6, 19, tzinfo=UTC))
    assert [r["round"] for r in found] == ["Women's Boulder Final", "Men's & Women's Lead Semi-Final"]
    assert found[0] == {"round": "Women's Boulder Final", "place": "Innsbruck, Austria", "stream": "https://youtu.be/x",
                        "starts": "2026-06-18T17:30:00+00:00", "ends": "2026-06-18T20:00:00+00:00"}
    assert climbing.rounds(CALENDAR, datetime(2026, 11, 4, tzinfo=UTC)) == []                   # out of season


# ---------- an issue ----------

def test_one_issue_a_day_kept_as_it_came_out_for_a_week(app, journals):
    assert dispatch.publish(NOW) == "2026-11-04"
    first = dispatch.issue(None)
    assert first["edition"] == {"id": "2026-11-04", "number": 308, "out": "2026-11-04T00:00:00+00:00",
                                "prev": None, "next": None}
    assert dispatch.publish(NOW + timedelta(hours=5)) is None                          # the day has its issue
    dispatch.publish(NOW + timedelta(days=1))
    again = dispatch.issue("2026-11-04")
    assert again["edition"]["next"] == "2026-11-05"
    assert all(again[k] == first[k] for k in ("stories", "print", "poem", "holidays"))  # unchanged
    for d in range(2, 9):
        dispatch.publish(NOW + timedelta(days=d))
    newest = dispatch.issue("2026-11-04")                                               # gone: the newest instead
    assert newest["edition"]["id"] == "2026-11-12" and newest["editions"] == [f"2026-11-{d:02}" for d in range(5, 13)]
    assert len({i.print["id"] for i in Issue.query}) == len({i.poem["title"] for i in Issue.query}) == 8


def test_an_issue_waits_for_the_journals_to_answer(app, journals, monkeypatch):
    def down(*_):
        raise requests.ConnectionError

    monkeypatch.setattr(papers, "europe_pmc", down)
    assert dispatch.publish(NOW) is None and Issue.query.count() == 0
    shown = dispatch.issue(None)
    assert shown["edition"] is None and "Nature" in shown["sources"]["failing"]
    monkeypatch.setattr(papers, "europe_pmc", lambda issn, since, until: [dict(r) for r in BY_ISSN.get(issn, [])])
    assert dispatch.publish(NOW + timedelta(minutes=10)) == "2026-11-04"                # the next round


def test_the_cover_story_leads_else_the_longest_read():
    a, b, c = (papers.story(i, "A journal", i, "", date(2026, 11, 4), abstract=text, cover=cover)
               for i, text, cover in (("a", "short", False), ("b", "a good deal longer", False), ("c", "", True)))
    assert [s["id"] for s in dispatch.front([a, b])] == ["b", "a"]
    assert [s["id"] for s in dispatch.front([a, b, c])] == ["c", "a", "b"]
    assert dispatch.front([]) == []


def test_each_has_its_turn_before_any_comes_again():
    items = list(range(7))
    start = date.fromordinal(7 * 105_000)                                               # a round begins
    rounds = [[dispatch.turn(items, "poem", start + timedelta(days=7 * r + d)) for d in range(7)] for r in range(3)]
    assert all(sorted(r) == items for r in rounds) and len({tuple(r) for r in rounds}) == 3    # each in its own order
    assert dispatch.turn(items, "poem", start) == rounds[0][0]                          # the same day, the same one


def test_the_holidays_ahead_are_hong_kongs_in_english():
    days = dispatch.holidays_ahead(date(2026, 12, 21))
    assert [d["date"] for d in days] == ["2026-12-25", "2026-12-26", "2027-01-01"]
    assert days[0]["name"] == "Christmas Day"
    assert dispatch.holidays_ahead(date(2027, 3, 22))[0]["name"] == "Good Friday"     # a general holiday counts too
    assert dispatch.holidays_ahead(date(2026, 11, 9)) == []                            # no section then


def test_the_poems_are_the_two_anthologies_in_hong_kongs_characters():
    books = [p["book"] for p in dispatch.POEMS]
    assert (books.count("唐詩"), books.count("宋詞")) == (320, 280)
    text = "".join("".join(p["lines"]) for p in dispatch.POEMS)
    assert "為" in text and "爲" not in text and "为" not in text                    # Hong Kong's forms, not simplified
    assert all(p["title"] and p["author"] and p["lines"] for p in dispatch.POEMS)


def test_the_prints_each_have_a_picture_and_the_museums_text():
    assert len({p["id"] for p in dispatch.PRINTS}) == len(dispatch.PRINTS) > 100
    for p in dispatch.PRINTS:
        assert p["title"] and len(p["text"]) >= 250 and 0.5 <= p["ratio"] <= 2
        assert p["figure"].startswith("https://openaccess-cdn.clevelandart.org/") and p["url"].startswith("https://")
        assert not re.search(r"<(?!/?i>)", p["title"] + p["maker"] + p["text"])        # only italics are kept


def test_an_issue_comes_out_at_eight_in_hong_kong():
    assert dispatch.out("2026-11-12") == datetime(2026, 11, 12, tzinfo=UTC)
    assert dispatch.latest(datetime(2026, 11, 11, 23, 59, tzinfo=UTC)) == "2026-11-11"
    assert dispatch.latest(datetime(2026, 11, 12, 0, 1, tzinfo=UTC)) == "2026-11-12"
