"""Dispatch: what an issue is made from (its sections: the journals' papers, a print, the poem, a
bird, the dates coming up), how it's made up each day and how it's kept. Each source is stood in
for by answers shaped like its own."""
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

import pytest
import requests

import birds
import dates
import dispatch
import papers
import poems
import prints
import sources
from journals import BY_KEY
from models import Issue, Paper, Shown, SourceStatus, db

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


class Answer:
    """What a source answers: its JSON."""
    def __init__(self, found):
        self.found = found

    def json(self):
        return self.found


def sheet(n: int, text: str | None = None, shape=(1200, 800)) -> dict:
    """A print as the museum's API lists and describes it."""
    key = f"1930.{n}"
    return {"accession_number": key, "title": f"Print {n}",
            "description": text or f"Print {n}, as the museum tells it. " * 9,
            "images": {"web": {"url": f"https://openaccess-cdn.clevelandart.org/{key}/{key}_web.jpg",
                               "width": shape[0], "height": shape[1]}},
            "creators": [{"description": "Katsushika Hokusai (Japanese, 1760–1849)"}], "creation_date": "early 1830s",
            "technique": "color woodblock print", "url": f"https://clevelandart.org/art/{key}"}


# Prints 1, 4 and 5 can be shown: 2 says too little, 3 is a handscroll, 6 is a set's second sheet.
MUSEUM = {"Japanese Art": [sheet(1), sheet(2, "Too short."), sheet(3, shape=(3000, 400)), sheet(4)],
          "Chinese Art": [sheet(5), sheet(6, sheet(5)["description"])]}


def museum(url, **params):
    if params:                                                    # the list of a department's prints
        return Answer({"data": MUSEUM[params["department"]]})
    return Answer({"data": next(a for d in MUSEUM.values() for a in d if url.endswith(a["accession_number"]))})


# Wikidata's id → English and Chinese titles (as Chinese Wikipedia writes it, and in Hong Kong's
# forms), Latin name and its photograph's licence: the peafowl's isn't open.
BIRDS = {"Q1": ("Eurasian pygmy owl", "花头鸺鹠", "花頭鵂鶹", "Glaucidium passerinum", "cc-by-sa-4.0"),
         "Q2": ("Indian peafowl", "蓝孔雀", "藍孔雀", "Pavo cristatus", "cc-by-nc-2.0"),
         "Q3": ("Common kingfisher", "普通翠鸟", "普通翠鳥", "Alcedo atthis", "cc0")}


def wiki(url, **params):
    if url == birds.WIKIDATA and "VALUES" not in params["query"]:                       # the list: ids alone
        ids = [{"bird": {"value": f"http://www.wikidata.org/entity/{q}"}} for q in BIRDS]
        return Answer({"results": {"bindings": ids}})
    if url == birds.WIKIDATA:                                                                # what's known of one
        q = re.search(r"wd:(Q\d+) ", params["query"]).group(1)
        en, zh, _, latin, _ = BIRDS[q]
        return Answer({"results": {"bindings": [{
            "latin": {"value": latin}, "status": {"value": "least concern"},
            "image": {"value": f"http://commons.wikimedia.org/wiki/Special:FilePath/{q}%20photo.jpg"},
            "en": {"value": f"https://en.wikipedia.org/wiki/{en.replace(' ', '_')}"},
            "zh": {"value": f"https://zh.wikipedia.org/wiki/{quote(zh)}"}}]}})
    if url == birds.COMMONS:
        q = params["titles"][len("File:"):].split()[0]
        return Answer({"query": {"pages": [{"imageinfo": [{
            "thumburl": f"https://upload.wikimedia.org/thumb/{q}.jpg/1200px-{q}.jpg?utm_source=commons", "width": 1200,
            "height": 800, "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{q}", "extmetadata": {
                "License": {"value": BIRDS[q][4]}, "LicenseShortName": {"value": "CC BY-SA 4.0"},
                "Artist": {"value": '<bdi><a href="https://example.org/q5">Charles J. Sharp</a></bdi>'}}}]}]}})
    if url == birds.ZH:
        hk = next(hk for _, zh, hk, _, _ in BIRDS.values() if zh == params["titles"])
        return Answer({"query": {"pages": [{"title": params["titles"], "varianttitles": {"zh-hk": hk}}]}})
    en = url[len(birds.SUMMARY):].replace("_", " ")
    return Answer({"title": en, "extract": f"The {en} is a bird & a fine one.",
                   "content_urls": {"desktop": {"page": f"https://en.wikipedia.org/wiki/{url[len(birds.SUMMARY):]}"}}})


@pytest.fixture
def journals(monkeypatch):
    """The journals, the publishers' pages and Crossref, the museum and Wikipedia, all stood in
    for; no dates."""
    monkeypatch.setattr(papers, "europe_pmc", lambda issn, since, until: [dict(r) for r in BY_ISSN.get(issn, [])])
    monkeypatch.setattr(dates, "take", lambda edition, day, now, report: [])
    monkeypatch.setattr(prints, "get", museum)
    monkeypatch.setattr(birds, "get", wiki)

    def get(url, **params):   # Europe PMC, asked which of a journal's papers some DOIs are
        issn, dois = re.search(r'ISSN:"(.+?)"', params["query"]).group(1), re.findall(r'DOI:"(.+?)"', params["query"])
        return Answer({"resultList": {"result": [r for r in BY_ISSN[issn] if r["doi"] in dois]}})

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
    assert (sources.clean("&lt;i&gt;Corvus&lt;/i&gt; <b>corone</b> & Ca<sup>2+</sup>")
            == "<i>Corvus</i> corone &amp; Ca<sup>2+</sup>")
    assert sources.clean('<script>x</script><a href="javascript:1">y</a>') == "x y"
    # Reference numbers go (their list isn't shown); a power of ten stays.
    assert sources.clean("well-being<sup>1</sup> and circuits<sup>3–5</sup>, 10<sup>6</sup> cells") == \
        "well-being and circuits, 10<sup>6</sup> cells"


def test_paragraphs_are_parted_and_links_dissolve():
    link = '<a href="https://example.org"><span>budget</span></a>'
    parts = [f"<span>The </span>{link}<span>, in <em>full</em>.</span>", "One line<br/>and the next", "", None]
    assert prints.paragraphs(parts) == "The budget, in <i>full</i>.\n\nOne line\nand the next"


def test_a_paper_belongs_by_its_words_and_never_when_its_the_environments():
    science, neuron = BY_KEY["science"], BY_KEY["neuron"]
    assert papers.belongs(science, "The phylogeny of early tetrapods", "")
    assert papers.belongs(science, "A long road", "Sexual selection and speciation shaped these lizards.")
    assert not papers.belongs(science, "A long road", "Evolution, once.")                 # one word in passing
    assert not papers.belongs(science, "Directed evolution of an enzyme", "A neural network designs it.")
    assert not papers.belongs(science, "Protected areas slow habitat loss for forest birds", "")
    assert not papers.belongs(neuron, "A circuit for thirst", "Under drought and pollution, climate change…")
    assert papers.belongs(neuron, "Sequence conservation of a cortical enhancer", "")     # not that conservation


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


def test_a_dispatch_is_told_by_its_title_and_is_on_the_last_out_of_the_papers_it_cites(app, journals, monkeypatch):
    monkeypatch.setitem(BY_ISSN, "0960-9822", [
        record("10.1016/j.cub.owl", "An Eocene owl hunted by day", issn="0960-9822"),
        record("10.1016/j.cub.eyes", "The eyes of owls", first="2025-03-01", printed="2025 Mar 1", issn="0960-9822"),
        record("10.1016/j.cub.d1", "Palaeobiology: An owl by day", abstract="Its eyes say so.", issn="0960-9822"),
        record("10.1016/j.cub.guide", "Stingless bees", abstract="A quick guide.", issn="0960-9822")])
    monkeypatch.setitem(REFERENCES, "10.1016/j.cub.d1", ["10.1016/j.cub.eyes", "10.1016/j.cub.owl"])
    papers.fetch(NOW)
    piece = db.session.get(Paper, "10.1016/j.cub.d1")
    assert (piece.kind, piece.on, piece.abstract) == ("commentary", "10.1016/j.cub.owl", "Its eyes say so.")
    assert db.session.get(Paper, "10.1016/j.cub.guide") is None                        # front matter


def test_a_piece_from_before_the_window_is_known_when_its_read_again(app, journals):
    papers.fetch(NOW)
    stored = Paper.query.count()
    assert papers.fetch(NOW + timedelta(days=15))["papers:nature"] == (2, None)        # Europe PMC took them in late
    assert Paper.query.count() == stored


def test_the_papers_waiting_are_spread_over_the_days_and_each_runs_once(app, journals):
    """Waiting are the cover story and the papers with a commentary (not neuron.1, which has none):
    the cover story and the longest out first, a third of them a day."""
    ran = []
    for d in range(4):
        assert dispatch.publish(NOW + timedelta(days=d)) == (NOW + timedelta(days=d)).date().isoformat()
        ran.append(dispatch.issue(None)["sections"]["papers"])
    assert [[s["id"] for s in stories] for stories in ran] == [
        ["10.1038/s41586-1", "10.1016/j.neuron.old"], ["10.1038/s41586-2"], ["10.1126/science.brain"], []]
    cover, late = ran[0]
    assert cover["cover"] and cover["cover_image"] == "https://img.example/cover.jpg" and cover["source"] == "Nature"
    assert not cover["earlier"]                                                 # a week old, no more
    assert late["earlier"] and late["commentary"][0]["label"] == "Preview"     # from August: its Preview is new
    [note] = ran[1][0]["commentary"]
    assert (note["label"], note["summary"]) == ("News & Views", "What the meta-study means.")   # from its page
    assert db.session.get(Paper, "10.1126/science.brain").ran == "2026-11-06"


def test_a_section_that_doesnt_answer_is_only_missed(app, monkeypatch):
    def down(url, **_):
        raise requests.ConnectionError

    for section in (dates, prints, birds):
        monkeypatch.setattr(section, "get", down)
    monkeypatch.setattr(papers, "fetch", lambda now: SourceStatus.record({"papers:nature": (1, None)}, now))
    assert dispatch.publish(NOW) == "2026-11-04"
    shown = dispatch.issue(None)
    assert sorted(shown["sections"]) == ["dates", "papers", "poem"] and shown["sections"]["dates"] == []
    assert shown["sources"]["failing"] == ["the Cleveland Museum of Art", "Wikipedia", "the climbing calendar",
                                           "the UFC’s schedule"]


# ---------- the dates ----------

def climb(name, kind, starts, disciplines=("boulder",)):
    return {"name": name, "kind": kind, "disciplines": list(disciplines), "starts_at": f"{starts}:00+02:00",
            "ends_at": f"{starts[:10]}T22:00:00+02:00", "stream_url": "https://youtu.be/x"}


CALENDAR = {"events": [
    {"league_name": dates.LEAGUE, "name": "World Climbing Series Innsbruck 2026", "location": "Innsbruck",
     "country_name": "Austria", "site_url": "https://ifsc.stream/innsbruck", "rounds": [
         climb("Women's Boulder Qualification", "qualification", "2026-06-18T09:00"),
         climb("Women's Boulder Final", "final", "2026-06-18T19:30"),                           # the 19th in Hong Kong
         climb("Men's Speed Final", "final", "2026-06-19T19:30", ("speed",)),
         climb("Men's & Women's Lead Semi-Final", "semi-final", "2026-06-20T10:00", ("lead",))]},
    {"league_name": "IFSC Paraclimbing", "name": "Paraclimbing Innsbruck 2026", "location": "Innsbruck",
     "country_name": "Austria", "rounds": [climb("Men's Lead Final", "final", "2026-06-19T16:00", ("lead",))]},
    {"league_name": dates.LEAGUE, "name": "World Climbing Series Speed 2026", "location": "Bali",
     "country_name": "Indonesia", "rounds": [climb("Men's Speed Final", "final", "2026-07-03T19:30", ("speed",))]},
]}


def bout(starts, *names):
    return {"date": starts, "competitors": [{"order": n, "athlete": {"displayName": name}}
                                            for n, name in reversed(list(enumerate(names, 1)))]}


SCHEDULE = {"events": [
    {"name": "UFC 334: Gane vs. Hokit", "date": "2026-11-15T02:00Z", "status": {"type": {"name": "STATUS_SCHEDULED"}},
     "venues": [{"address": {"city": "New York", "country": "USA"}}], "links": [{"href": "https://espn.example/334"}],
     "competitions": [bout("2026-11-14T22:00Z", "One", "Two"), bout("2026-11-15T02:00Z", "Harrison", "Nunes"),
                      bout("2026-11-15T02:00Z", "Gane", "Hokit")]},
    {"name": "Dana White's Contender Series", "date": "2026-11-18T00:00Z", "competitions": [],
     "status": {"type": {"name": "STATUS_SCHEDULED"}}},
    {"name": "UFC Fight Night: Off", "date": "2026-11-21T18:00Z", "status": {"type": {"name": "STATUS_CANCELED"}}},
]}


def test_the_climbing_is_the_world_cups_boulder_and_lead_semis_and_finals():
    [cup] = dates.climbing(CALENDAR)
    assert (cup["kind"], cup["title"], cup["place"]) == ("Climbing", "World Climbing Series Innsbruck 2026",
                                                         "Innsbruck, Austria")
    assert (cup["day"], cup["until"], cup["note"]) == ("2026-06-19", "2026-06-20", "Boulder and lead")
    assert cup["link"] == "https://ifsc.stream/innsbruck"
    assert cup["parts"] == [
        {"name": "Women's Boulder Final", "starts": "2026-06-18T17:30:00+00:00", "ends": "2026-06-18T20:00:00+00:00",
         "link": "https://youtu.be/x"},
        {"name": "Men's & Women's Lead Semi-Final", "starts": "2026-06-20T08:00:00+00:00",
         "ends": "2026-06-20T20:00:00+00:00", "link": "https://youtu.be/x"}]


def test_a_ufc_card_is_on_the_day_of_its_main_card_with_its_bouts():
    [card] = dates.cards(SCHEDULE)                                                      # no Contender Series
    assert (card["kind"], card["title"], card["day"], card["until"]) == ("UFC", "UFC 334: Gane vs. Hokit",
                                                                         "2026-11-15", "2026-11-15")
    assert (card["place"], card["link"]) == ("New York, USA", "https://espn.example/334")
    assert card["note"] == "Gane vs. Hokit; Harrison vs. Nunes"                         # the headline first, no prelims
    assert card["parts"] == [{"name": "Prelims", "starts": "2026-11-14T22:00:00+00:00", "ends": None, "link": None},
                             {"name": "Main card", "starts": "2026-11-15T02:00:00+00:00", "ends": None, "link": None}]


def test_hong_kongs_holidays_run_together_as_breaks():
    christmas, new_year = dates.breaks(date(2026, 12, 21), date(2027, 1, 4))
    assert (christmas["title"], christmas["day"], christmas["until"]) == ("Christmas Day", "2026-12-25", "2026-12-26")
    assert christmas["note"] == "The first weekday after Christmas Day"
    assert (new_year["title"], new_year["day"], new_year["until"], new_year["note"]) == (
        "The first day of January", "2027-01-01", "2027-01-01", None)
    [lunar] = dates.breaks(date(2027, 2, 1), date(2027, 2, 15))                         # Saturday to Tuesday
    assert (lunar["title"], lunar["day"], lunar["until"]) == ("Lunar New Year's Day", "2027-02-06", "2027-02-09")
    [easter] = dates.breaks(date(2027, 3, 27), date(2027, 3, 27))                       # general holidays; begun
    assert (easter["title"], easter["day"], easter["until"]) == ("Good Friday", "2027-03-26", "2027-03-29")
    assert dates.breaks(date(2026, 11, 9), date(2026, 11, 23)) == []


def test_a_date_is_said_two_weeks_a_week_and_a_day_ahead_and_while_its_on():
    cup = dates.entry("Climbing", "A cup", date(2026, 6, 19), date(2026, 6, 21))
    said = [d for d in range(1, 31) if dates.due(cup, date(2026, 6, d))]
    assert said == [5, 12, 18, 19, 20, 21]


def test_the_dates_ahead_are_the_three_calendars_soonest_first(app, monkeypatch):
    asked = {}

    def get(url, **params):
        asked[url] = params
        return Answer(SCHEDULE if url == dates.UFC else CALENDAR)

    monkeypatch.setattr(dates, "get", get)
    monkeypatch.setattr(dates, "breaks", lambda first, last: [dates.entry("Holiday", "A day off", date(2026, 11, 14))])
    day, report = date(2026, 11, 14), {}
    found = [d["title"] for d in dates.take("", day, NOW, report)]
    assert found == ["A day off", "UFC 334: Gane vs. Hokit"]                            # no climbing due
    assert asked[dates.UFC] == {"dates": "20261113-20261129"}
    assert report == {"dates:climbing": (1, None), "dates:ufc": (1, None)}
    assert [d["title"] for d in dates.take("", date(2026, 11, 10), NOW, {})] == []     # four days ahead isn't a beat


# ---------- an issue ----------

def test_one_issue_a_day_kept_as_it_came_out_seven_at_a_time(app, journals):
    assert dispatch.publish(NOW) == "2026-11-04"
    first = dispatch.issue(None)
    assert first["edition"] == {"id": "2026-11-04", "number": 308, "out": "2026-11-04T00:00:00+00:00",
                                "prev": None, "next": None}
    assert dispatch.publish(NOW + timedelta(hours=5)) is None                          # the day has its issue
    dispatch.publish(NOW + timedelta(days=1))
    again = dispatch.issue("2026-11-04")
    assert again["edition"]["next"] == "2026-11-05"
    assert again["sections"] == first["sections"]                                       # unchanged
    assert sorted(first["sections"]) == ["bird", "dates", "papers", "poem", "print"]
    for d in range(2, 9):
        dispatch.publish(NOW + timedelta(days=d))
    newest = dispatch.issue("2026-11-04")                                               # gone: the newest instead
    assert newest["edition"]["id"] == "2026-11-12" and newest["editions"] == [f"2026-11-{d:02}" for d in range(6, 13)]
    assert len({i.sections["poem"]["title"] for i in Issue.query}) == 7                # a poem each, none twice


def test_an_issue_waits_for_the_journals_to_answer(app, journals, monkeypatch):
    def down(*_):
        raise requests.ConnectionError

    monkeypatch.setattr(papers, "europe_pmc", down)
    assert dispatch.publish(NOW) is None and Issue.query.count() == 0
    shown = dispatch.issue(None)
    assert shown["edition"] is None and "Nature" in shown["sources"]["failing"]
    monkeypatch.setattr(papers, "europe_pmc", lambda issn, since, until: [dict(r) for r in BY_ISSN.get(issn, [])])
    assert dispatch.publish(NOW + timedelta(minutes=10)) == "2026-11-04"                # the next round


def test_an_issue_is_all_its_sections_or_none(app, journals, monkeypatch):
    take = poems.take
    monkeypatch.setattr(poems, "take", lambda *_: 1 / 0)                                # the issue can't be made up
    with pytest.raises(ZeroDivisionError):
        dispatch.publish(NOW)
    db.session.rollback()
    assert Paper.query.count() and not Paper.query.filter(Paper.ran.isnot(None)).count()
    assert not Shown.query.count() and not SourceStatus.query.filter(SourceStatus.key.like("print:%")).count()
    monkeypatch.setattr(poems, "take", take)
    assert dispatch.publish(NOW + timedelta(minutes=10)) == "2026-11-04"                # the next round, with them
    assert Paper.query.filter_by(ran="2026-11-04").count()
    assert {r.section for r in Shown.query} == {"print", "poem", "bird"}


def test_a_print_is_one_the_museum_wrote_about_shown_whole(app, journals):
    shown = []
    for d in range(4):
        dispatch.publish(NOW + timedelta(days=d))
        shown.append(dispatch.issue(None)["sections"]["print"])
    assert sorted(p["id"] for p in shown[:3]) == ["1930.1", "1930.4", "1930.5"]       # each once, then a new round
    assert shown[3]["id"] in {"1930.1", "1930.4", "1930.5"}
    p = shown[0]
    assert (p["source"], p["ratio"], p["title"]) == ("Cleveland Museum of Art", 1.5, p["id"].replace("1930.", "Print "))
    assert p["maker"] == "Katsushika Hokusai (Japanese, 1760–1849)"
    assert p["figure"].startswith("https://openaccess-cdn.clevelandart.org/") and len(p["text"]) >= prints.WORTH_READING
    assert db.session.get(SourceStatus, "print:cma").items == 3


def test_a_bird_is_a_known_one_with_its_names_and_a_photograph_it_may_show(app, journals):
    shown = []
    for d in range(3):
        dispatch.publish(NOW + timedelta(days=d))
        shown.append(dispatch.issue(None)["sections"].get("bird"))
    assert sorted(b["id"] for b in shown[:2]) == ["Q1", "Q3"] and shown[2]["id"] in {"Q1", "Q3"}   # never the peafowl
    owl = next(b for b in shown if b["id"] == "Q1")
    assert (owl["title"], owl["zh"], owl["status"]) == ("Eurasian pygmy owl", "花頭鵂鶹", "Least concern")
    assert owl["latin"] == "Glaucidium passerinum"
    assert owl["text"] == "The Eurasian pygmy owl is a bird &amp; a fine one."           # safe to put in the page
    assert owl["figure"] == "https://upload.wikimedia.org/thumb/Q1.jpg/1200px-Q1.jpg" and owl["ratio"] == 1.5
    assert (owl["credit"], owl["licence"], owl["url"]) == ("Charles J. Sharp", "CC BY-SA 4.0",
                                                          "https://en.wikipedia.org/wiki/Eurasian_pygmy_owl")


def test_a_birds_account_is_its_first_sentences():
    long = "A duck of the Ganges. " * 40
    assert birds._first(long) == ("A duck of the Ganges. " * 22).strip() and len(birds._first(long)) <= birds.SHORT
    one = "One sentence that runs on " * 30
    assert birds._first(one) == one.strip()                                             # one, however long


def test_the_cover_story_leads_else_the_longest_read():
    a, b, c = ({"id": i, "abstract": text, "cover": cover}
               for i, text, cover in (("a", "short", False), ("b", "a good deal longer", False), ("c", "", True)))
    assert [s["id"] for s in papers.front([a, b])] == ["b", "a"]
    assert [s["id"] for s in papers.front([a, b, c])] == ["c", "a", "b"]
    assert papers.front([]) == []


def test_each_runs_once_before_any_comes_again(app):
    keys = [str(k) for k in range(7)]
    assert Shown.order("poem", keys, "2026-11-04") == Shown.order("poem", keys, "2026-11-04")   # an edition's own order
    rounds = []
    for r in range(3):
        rounds.append([])
        for d in range(7):
            edition = f"{2026 + r}-11-{d + 1:02}"
            rounds[-1].append(Shown.order("poem", keys, edition)[0])
            Shown.mark("poem", rounds[-1][-1], edition)
    assert all(sorted(r) == keys for r in rounds) and len({tuple(r) for r in rounds}) == 3    # each in its own order


def test_the_poems_are_the_two_anthologies_and_the_passages_in_hong_kongs_characters():
    books = [p["book"] for p in poems.POEMS]
    assert [books.count(b) for b in ("唐詩三百首", "宋詞三百首", "古文觀止")] == [320, 283, 66]
    text = "".join("".join(p["lines"]) for p in poems.POEMS)
    assert "為" in text and "爲" not in text and "为" not in text                    # Hong Kong's forms, not simplified
    assert "明月松間照" in text and "明月鬆間照" not in text          # typed in traditional, not converted to it
    # An empty line is a break between stanzas.
    assert all(p["title"] and p["author"] and p["lines"][0] and p["lines"][-1] for p in poems.POEMS)


def test_a_passages_notes_are_on_its_own_words_in_reading_order():
    for p in poems.POEMS:
        assert ("notes" in p) == (p["book"] == "古文觀止")
        spans = [(n["line"], n["start"], n["end"]) for n in p.get("notes", [])]
        assert spans == sorted(spans)
        assert all(a[0] < b[0] or a[2] <= b[1] for a, b in zip(spans, spans[1:], strict=False))     # none over another
        for n in p.get("notes", []):
            word = p["lines"][n["line"]][n["start"]:n["end"]]
            assert word and all(s["word"] in word and s["def"] for s in n["senses"])


def test_an_issue_comes_out_at_eight_in_hong_kong():
    assert dispatch.out("2026-11-12") == datetime(2026, 11, 12, tzinfo=UTC)
    assert dispatch.latest(datetime(2026, 11, 11, 23, 59, tzinfo=UTC)) == "2026-11-11"
    assert dispatch.latest(datetime(2026, 11, 12, 0, 1, tzinfo=UTC)) == "2026-11-12"
