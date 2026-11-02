"""Papers: which papers make an edition, how commentary finds its paper, and how an edition is ordered.
The journals are stood in for by records shaped like Europe PMC's."""
from datetime import UTC, date, datetime

import pytest

import papers
from models import Paper, db

LONG = "Neurons in the cortex " + "and their synapses " * 30     # an abstract, not front matter
NOW = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)                     # a Monday: last week's edition is out


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
REFERENCES = {"10.1016/j.neuron.p1": ["10.1016/j.neuron.old", "10.1002/elsewhere"],
              "10.1038/d41586-nv": ["10.1038/s41586-2"]}


@pytest.fixture
def journals(monkeypatch):
    """The journals, the publishers' pages and Crossref, all stood in for."""
    monkeypatch.setattr(papers, "europe_pmc", lambda issn, since, until: [dict(r) for r in BY_ISSN[issn]])

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
                        lambda url: {"dc.type": "News And Views" if url.endswith("-nv") else "News"})
    monkeypatch.setattr(papers, "references", lambda doi: REFERENCES.get(doi, []))
    monkeypatch.setattr(papers, "figure_for", lambda doi: f"https://img.example/{doi}.png")
    monkeypatch.setattr(papers, "cover_story", lambda url: ("10.1038/s41586-1", "https://img.example/cover.jpg"))


def test_clean_keeps_only_italics_and_scripts():
    assert (papers.clean("&lt;i&gt;Corvus&lt;/i&gt; <b>corone</b> & Ca<sup>2+</sup>")
            == "<i>Corvus</i> corone &amp; Ca<sup>2+</sup>")
    assert papers.clean('<script>x</script><a href="javascript:1">y</a>') == "x y"
    # Reference numbers go (their list isn't shown); a power of ten stays.
    assert papers.clean("well-being<sup>1</sup> and circuits<sup>3–5</sup>, 10<sup>6</sup> cells") == \
        "well-being and circuits, 10<sup>6</sup> cells"


def test_the_brain_words():
    assert papers.about_the_brain("Ultrarapid decision-making in an avian brain", "")
    assert papers.about_the_brain("A new lens", "Cortical neurons fire. Their synapses change.")
    assert not papers.about_the_brain("A new lens", "Our neural network learns cortical maps.")   # only one word
    assert not papers.about_the_brain("Neural networks for materials", "Graphs and crystals.")


def test_kinds():
    assert papers.kind_of(NEURON[0]) == "research"
    assert papers.kind_of(NEURON[2]) == "commentary"
    assert papers.kind_of(NEURON[3]) == "other"
    assert papers.kind_of(SCIENCE[3]) == "news"
    assert papers.kind_of(record("x", "In Science Journals", abstract="Short.")) == "other"   # front matter


def test_a_print_month_gives_way_to_the_day_it_came():
    assert papers.published_on(NEURON[2]) == date(2026, 10, 28)
    assert papers.published_on(NEURON[0]) == date(2026, 10, 27)


def test_fetch_stores_the_neuroscience_and_ties_commentary(app, journals):
    with app.app_context():
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
        assert first.free["label"] == "PubMed Central" and first.figure
        assert db.session.get(Paper, "10.1038/s41586-1").cover == "https://img.example/cover.jpg"


def test_an_edition_leads_with_the_cover_then_commentary(app, journals):
    with app.app_context():
        papers.fetch(NOW)
        e = papers.edition(None, NOW.date())
        assert e["edition"]["id"] == "2026-W44" and e["edition"]["start"] == "2026-10-26"
        order = [p["doi"] for p in e["papers"]]
        assert order[0] == "10.1038/s41586-1"                                   # the cover story
        assert set(order[1:3]) == {"10.1038/s41586-2", "10.1126/science.brain"}    # with commentary
        assert order[3:] == ["10.1016/j.neuron.1"]
        assert [p["tier"] for p in e["papers"]] == ["lead", "major", "major", "minor"]
        assert e["papers"][1]["commentary"][0]["label"] == "News & Views"
        # The Preview came out this edition's week, on a paper from August.
        [late] = e["discussed"]
        assert late["doi"] == "10.1016/j.neuron.old" and late["commentary"][0]["label"] == "Preview"
        assert late["edition"] is None                                         # before the weeks read in full


def test_editions_are_finished_weeks_read_in_full(app, journals):
    with app.app_context():
        papers.fetch(NOW)                       # read from 12 Oct (a Monday): W42 on
        assert papers.editions(NOW.date()) == ["2026-W44"]
        assert papers.editions(date(2026, 10, 30)) == []          # W44 isn't over yet
        db.session.add(Paper(doi="x", journal="neuron", kind="research", title="x", published=date(2026, 10, 13)))
        db.session.commit()
        assert papers.editions(NOW.date()) == ["2026-W42", "2026-W44"]
        assert papers.edition("2026-W42", NOW.date())["edition"]["next"] == "2026-W44"


def test_the_page_and_its_api(app, journals):
    client = app.test_client()
    with app.app_context():
        papers.fetch(NOW)
    assert client.get("/papers").status_code == 200
    data = client.get("/api/papers?edition=nonsense").get_json()
    assert data["sources"] == {"ok": 3, "total": 3, "failing": []}
    assert data["edition"] is None or data["edition"]["id"] in data["editions"]
