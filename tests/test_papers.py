"""Papers and the issue they lead (dispatch.py): which papers make an issue, how commentary finds its
paper, how an issue is ordered and kept, and the holidays in it. The journals are stood in for by
records shaped like Europe PMC's."""
from datetime import UTC, datetime

import pytest

import dispatch
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
                        lambda url: {"dc.type": "News And Views" if url.endswith("-nv") else "News",
                                     "description": "What the meta-study means."})
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
        e = dispatch.issue(None, NOW.date())
        assert e["edition"]["id"] == "2026-W44" and e["edition"]["start"] == "2026-10-26"
        order = [p["doi"] for p in e["papers"]]
        assert order[0] == "10.1038/s41586-1"                                   # the cover story
        # with commentary: two of the week's papers, and one from August whose Preview came out this week
        assert set(order[1:4]) == {"10.1038/s41586-2", "10.1126/science.brain", "10.1016/j.neuron.old"}
        assert order[4:] == ["10.1016/j.neuron.1"]
        by = {p["doi"]: p for p in e["papers"]}
        assert by["10.1038/s41586-2"]["commentary"][0]["label"] == "News & Views"
        assert by["10.1038/s41586-2"]["commentary"][0]["summary"] == "What the meta-study means."   # from its page
        late = by["10.1016/j.neuron.old"]
        assert late["earlier"] and late["commentary"][0]["label"] == "Preview"   # in the issue its commentary is in
        assert not by["10.1038/s41586-2"]["earlier"]
        assert "discussed" not in e


def test_only_the_last_four_issues_are_kept(app, journals):
    """Dispatch is a periodical, not an archive: older papers go, unless a commentary on
    one is in an issue kept (that issue shows it)."""
    from datetime import date
    with app.app_context():
        papers.fetch(NOW)
        for doi, kind in (("10.1016/j.neuron.sept", "research"), ("10.1016/j.neuron.note", "other")):
            db.session.add(Paper(doi=doi, journal="neuron", kind=kind, title="Old", published=date(2026, 9, 1)))
        db.session.commit()
        assert dispatch.prune(NOW.date()) == 2
        db.session.commit()
        assert db.session.get(Paper, "10.1016/j.neuron.sept") is None
        assert db.session.get(Paper, "10.1016/j.neuron.old")             # from August, but its Preview is recent
        assert all(dispatch.week_of(e) >= dispatch.kept_from(NOW.date()) for e in dispatch.editions(NOW.date()))


def test_the_holidays_ahead_are_hong_kongs_in_english():
    from datetime import date
    days = dispatch.holidays_ahead(date(2026, 12, 21))
    assert [d["date"] for d in days] == ["2026-12-25", "2026-12-26", "2027-01-01"]
    assert days[0]["name"] == "Christmas Day"
    assert dispatch.holidays_ahead(date(2027, 3, 22))[0]["name"] == "Good Friday"     # a general holiday counts too
    assert dispatch.holidays_ahead(date(2026, 11, 9)) == []                            # no section that week
