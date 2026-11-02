"""Papers: a weekly paper of the neuroscience in the journals in journals.py.

Once a day the past three weeks of each journal are read from Europe PMC,
which has their abstracts, article types, free copies and the commentary links
PubMed keeps ("Comment on"). Each paper belongs to the week it first came out
(online or in print). A week is an edition, filling in while it's under way. Nothing is
ranked by guesswork: the cover story leads, then the papers the journal
commissioned a commentary on, then the rest.
"""
import html
import re
from datetime import UTC, date, datetime, timedelta

import feedparser
import requests

from journals import BY_KEY, JOURNALS, NEURO_WORDS
from models import Paper, SourceStatus, db, utcnow
from sources import USER_AGENT

EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CROSSREF = "https://api.crossref.org/works/"
WINDOW = timedelta(days=21)         # how far back each fetch looks: commentaries and free copies come later
EVERY = timedelta(days=1)          # a weekly paper: once a day is plenty
TIMEOUT = 20
NOT_RESEARCH = {"Review", "News", "Editorial", "Letter", "Published Erratum", "Retraction of Publication",
                "Retracted Publication", "Expression of Concern", "Comment"}


def get(url, **params):
    resp = requests.get(url, params=params or None, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp


# ---------- text ----------

_KEEP = re.compile(r"(</?(?:i|sub|sup)>)", re.I)


def clean(raw: str | None) -> str:
    """Plain text, safe to put in a page, keeping only italics, sub- and superscripts."""
    text = html.unescape(raw or "")
    text = re.sub(r"<(/?)em>", r"<\1i>", text, flags=re.I)
    # Reference numbers ("well-being<sup>1</sup>", "circuits<sup>3–5</sup>") point to a list that isn't here.
    text = re.sub(r"(?<=[^\d\s])<sup>[\d,\u2013\u2012-]+</sup>", "", text, flags=re.I)
    out = []
    for part in _KEEP.split(text):
        if _KEEP.fullmatch(part):
            out.append(part.lower())
        else:
            out.append(html.escape(re.sub(r"<[^>]*>", " ", part), quote=False))
    return re.sub(r"\s+", " ", "".join(out)).strip()


def plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(text or ""))


def about_the_brain(title: str, abstract: str) -> bool:
    """The "words" scope: a nervous-system word in the title, or two different ones in the abstract."""
    if NEURO_WORDS.search(plain(title)):
        return True
    return len({m.lower() for m in NEURO_WORDS.findall(plain(abstract))}) >= 2


# ---------- what Europe PMC says ----------

def europe_pmc(issn: str, since: date, until: date) -> list[dict]:
    """Everything of a journal's that Europe PMC took in between two days, with abstracts, all pages.
    (By the day it came in: a piece only in print is dated the 1st of its issue's month.)"""
    query = f'ISSN:"{issn}" AND FIRST_IDATE:[{since.isoformat()} TO {until.isoformat()}]'
    out, cursor = [], "*"
    while True:
        page = get(EUROPE_PMC, query=query, format="json", resultType="core", pageSize=1000, cursorMark=cursor).json()
        out += page.get("resultList", {}).get("result", [])
        nxt = page.get("nextCursorMark")
        if not nxt or nxt == cursor or not page.get("resultList", {}).get("result"):
            return out
        cursor = nxt


def kind_of(record: dict) -> str:
    """research, commentary, news (a journal's news pages, where Nature files News & Views) or other."""
    types = set(record.get("pubTypeList", {}).get("pubType", []))
    if "Comment" in types:
        return "commentary"
    if "News" in types:
        return "news"
    if types & NOT_RESEARCH:
        return "other"
    if "research-article" in types or len(record.get("abstractText") or "") >= 400:
        return "research"
    return "other"   # a journal's front matter has no abstract worth the name


def _links(record: dict, kind: str) -> list[str]:
    """The DOIs in a record's PubMed links of one kind ("Comment on", "Preprint in"…)."""
    out = []
    for c in record.get("commentCorrectionList", {}).get("commentCorrection", []):
        if c.get("type") == kind:
            m = re.search(r"doi:\s*(10\.\S+?)\.?$", c.get("reference", ""))
            if m:
                out.append(m.group(1).lower())
            elif c.get("source") == "PPR" and c.get("id"):
                out.append(f"PPR:{c['id']}")
    return out


def free_copy(record: dict) -> dict | None:
    """Where anyone can read it: PubMed Central, the journal itself, or a preprint."""
    if record.get("pmcid"):
        return {"url": f"https://pmc.ncbi.nlm.nih.gov/articles/{record['pmcid']}/", "label": "PubMed Central"}
    if record.get("isOpenAccess") == "Y":
        return {"url": f"https://doi.org/{record['doi']}", "label": "Open access"}
    for ref in _links(record, "Update of") + _links(record, "Preprint in"):
        if ref.startswith("PPR:"):
            return {"url": f"https://europepmc.org/article/PPR/{ref[4:]}", "label": "Preprint"}
        return {"url": f"https://doi.org/{ref}", "label": "Preprint"}
    return None


def surnames(record: dict) -> list[str]:
    authors = record.get("authorList", {}).get("author", [])
    return [a.get("lastName") or a.get("collectiveName") or a.get("fullName", "") for a in authors][:200]


def published_on(record: dict) -> date | None:
    """The day it came out: online if it was, else in print. A print date that's only a
    month ("2026 Oct", stored as the 1st) gives way to the day Europe PMC got it."""
    online = _date(record.get("electronicPublicationDate"))
    if online:
        return online
    first = _date(record.get("firstPublicationDate"))
    month_only = not re.search(r"\d{4} \w{3} \d", record.get("journalInfo", {}).get("dateOfPublication", ""))
    if first and first.day == 1 and month_only:
        return _date(record.get("dateOfCreation")) or first
    return first


def _date(text: str | None) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


# ---------- the publishers' own pages ----------

def page_meta(url: str) -> dict:
    """A page's <meta> name/property → content (the first of each)."""
    text = get(url).text
    meta = {}
    for m in re.finditer(r"<meta\s+(?:name|property)=\"([^\"]+)\"\s+content=\"([^\"]*)\"", text):
        meta.setdefault(m.group(1), html.unescape(m.group(2)))
    return meta


def subject_dois(feed_url: str) -> set[str]:
    """The DOIs in a publisher's subject feed (Nature's neuroscience papers)."""
    feed = feedparser.parse(get(feed_url).content)
    out = set()
    for e in feed.entries:
        m = re.search(r"/articles/([\w.-]+)$", e.get("link", ""))
        if m:
            out.add(f"10.1038/{m.group(1)}".lower())
    return out


def cover_story(page_url: str) -> tuple[str, str] | None:
    """The DOI of the current issue's cover story, and the cover image, from the issue's page."""
    text = get(page_url).text
    i = text.find('data-test="issue-description"')
    if i < 0:
        return None
    m = re.search(r'href="https://doi\.org/(10\.[^"]+)"', text[i:i + 6000])
    img = re.search(r'<img src="([^"]+)"[^>]*data-test="issue-cover-image"', text[i:i + 8000])
    return (m.group(1).lower(), img.group(1) if img else None) if m else None


def references(doi: str) -> list[str]:
    """The DOIs a piece cites, from Crossref."""
    refs = get(CROSSREF + doi).json()["message"].get("reference", [])
    return [r["DOI"].lower() for r in refs if r.get("DOI")]


def figure_for(doi: str) -> str | None:
    """An image of the paper's graphical abstract or first figure: Cell Press's from
    Elsevier's image server, anyone else's from the og:image on the paper's page."""
    if doi.startswith("10.1016/"):
        ids = get(CROSSREF + doi).json()["message"].get("alternative-id", [])
        pii = next((i for i in ids if re.fullmatch(r"S\w{16}", i)), None)
        for name in ("ga1.jpg", "gr1.jpg"):
            url = f"https://ars.els-cdn.com/content/image/1-s2.0-{pii}-{name}" if pii else None
            if url and requests.head(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}).ok:
                return url
        return None
    try:
        image = page_meta(f"https://doi.org/{doi}").get("og:image", "")
    except requests.HTTPError:
        return None         # a publisher that turns readers like this away (Science)
    return image if re.match(r"https://", image) and re.search(r"Fig|fig|MediaObjects", image) else None


# ---------- a fetch ----------

def fetch(now: datetime | None = None, window: timedelta = WINDOW) -> dict:
    """Read every journal for the last `window`, store what's new, link commentaries
    to their papers, find figures and the cover story. Returns a count per journal."""
    now = now or datetime.now(UTC)
    since, until = (now - window).date(), now.date()
    report = {}
    for j in JOURNALS:
        status = db.session.get(SourceStatus, f"papers:{j.key}") or SourceStatus(key=f"papers:{j.key}")
        db.session.add(status)
        status.last_try = now.replace(tzinfo=None)
        try:
            report[j.key] = _read(j, since, until)
            status.last_ok, status.items, status.error = now.replace(tzinfo=None), report[j.key], None
        except (requests.RequestException, ValueError, KeyError) as e:
            status.error = type(e).__name__
            report[j.key] = status.error
        db.session.commit()
    covered = db.session.get(SourceStatus, "papers:since") or SourceStatus(key="papers:since")
    start = datetime.combine(since, datetime.min.time())
    covered.last_ok = min(covered.last_ok or start, start)    # the earliest day ever read: where editions begin
    db.session.add(covered)
    _link_commentaries(since)
    _enrich()
    _covers()
    db.session.commit()
    return report


def _read(j, since: date, until: date) -> int:
    """Store a journal's research papers (those in scope) and commentaries; returns how many papers it has."""
    known = {p.doi: p for p in Paper.query.filter(Paper.journal == j.key, Paper.published >= since)}
    in_subject = subject_dois(j.subject_feed) if j.scope == "subject" else set()
    count = 0
    for r in europe_pmc(j.issn, since, until):
        doi = (r.get("doi") or "").lower()
        published = published_on(r)
        if not doi or not published:
            continue
        kind = kind_of(r)
        title, abstract = clean(r.get("title")), clean(r.get("abstractText"))
        if kind == "research":
            if j.scope == "subject" and doi not in in_subject and doi not in known:
                continue
            if j.scope == "words" and not about_the_brain(title, abstract):
                continue
            count += 1
        elif kind == "news":
            # Filed with the news (Nature's News & Views): its page says which it is,
            # looked at once for each piece the subject feed lists.
            if not j.commentary_page_type or doi in known or doi not in in_subject:
                continue
            page_type = page_meta(f"https://doi.org/{doi}").get("dc.type")
            kind = "commentary" if page_type == j.commentary_page_type else "other"
            if kind == "other":
                known[doi] = Paper(doi=doi, journal=j.key, kind="other", title=title, published=published)
                db.session.add(known[doi])
                continue
        elif kind != "commentary":
            continue
        known[doi] = _store(j, r, kind, known.get(doi))
    db.session.commit()
    return count


def _store(j, r: dict, kind: str, p: Paper | None = None) -> Paper:
    """Save what Europe PMC says about a research paper or commentary."""
    doi = r["doi"].lower()
    p = p or db.session.get(Paper, doi) or Paper(doi=doi, journal=j.key, kind=kind)
    if p.kind == "other":
        return p
    p.title, p.published = clean(r.get("title")).rstrip("."), published_on(r)
    p.authors, p.pmid, p.url = surnames(r), r.get("pmid"), f"https://doi.org/{doi}"
    if kind == "research":
        p.abstract, p.free = clean(r.get("abstractText")), free_copy(r)
    else:
        on = _links(r, "Comment on")
        p.on = p.on or (on[0] if on else None)
    db.session.add(p)
    return p


def _adopt(doi: str) -> bool:
    """Add a paper a commentary is about, from before the window: when it's research
    in one of the journals read here (and, for "words", about the brain)."""
    found = get(EUROPE_PMC, query=f'DOI:"{doi}"', format="json", resultType="core").json()
    r = next(iter(found.get("resultList", {}).get("result", [])), None)
    info = (r or {}).get("journalInfo", {}).get("journal", {})
    j = next((j for j in JOURNALS if j.issn in (info.get("issn"), info.get("essn"))), None)
    if not (r and j and published_on(r) and kind_of(r) == "research"):
        return False
    if j.scope == "words" and not about_the_brain(r.get("title", ""), r.get("abstractText", "")):
        return False
    _store(j, r, "research")
    return True


def _link_commentaries(since: date):
    """Tie each commentary to its paper: the one PubMed links it to, else the first of
    its first few references that's a paper in these journals. A paper from before
    the window is added, so its commentary has something to point to."""
    research = {d for (d,) in db.session.query(Paper.doi).filter(Paper.kind == "research")}
    for c in Paper.query.filter(Paper.kind == "commentary", Paper.published >= since):
        if c.on in research:
            continue
        try:
            for doi in [c.on] if c.on else references(c.doi)[:3]:
                if doi in research or _adopt(doi):
                    c.on = doi
                    research.add(doi)
                    break
        except (requests.RequestException, ValueError, KeyError):
            continue
    db.session.commit()


def _enrich(limit: int = 60):
    """Look once for each new paper's figure."""
    for p in Paper.query.filter(Paper.kind == "research", Paper.enriched.is_(None)).limit(limit):
        try:
            p.figure = figure_for(p.doi)
        except (requests.RequestException, ValueError, KeyError):
            continue        # tried again next time
        p.enriched = utcnow()


def _covers():
    for j in JOURNALS:
        if not j.cover_page:
            continue
        try:
            found = cover_story(j.cover_page)
        except requests.RequestException:
            continue
        p = found and db.session.get(Paper, found[0])
        if p and p.kind == "research":
            p.cover = found[1] or p.figure or "yes"


def due(now: datetime) -> bool:
    last = (db.session.query(db.func.max(SourceStatus.last_try))
            .filter(SourceStatus.key.like("papers:%"), SourceStatus.key != "papers:since").scalar())
    return last is None or now.replace(tzinfo=None) - last >= EVERY


# ---------- editions ----------

def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def edition_id(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_of(edition: str) -> date | None:
    try:
        return date.fromisocalendar(int(edition[:4]), int(edition[6:8]), 1)
    except (ValueError, IndexError):
        return None


def editions(today: date) -> list[str]:
    """Every week read in full that has a paper in it, oldest first: this week too, while it fills in."""
    covered = db.session.get(SourceStatus, "papers:since")
    if not covered or not covered.last_ok:
        return []
    first = covered.last_ok.date()
    first = first if first.weekday() == 0 else monday(first) + timedelta(days=7)
    days = (db.session.query(Paper.published).distinct()
            .filter(Paper.kind == "research", Paper.published >= first,
                    Paper.published < monday(today) + timedelta(days=7)))
    return sorted({edition_id(d) for (d,) in days})


def _commentary(p: Paper, notes: list[Paper]) -> list[dict]:
    return [{"title": n.title, "url": n.url, "label": BY_KEY[n.journal].commentary, "journal": BY_KEY[n.journal].name,
             "authors": n.authors or []} for n in notes]


def edition(which: str | None, today: date, fetched: datetime | None = None) -> dict:
    """One week's paper: its papers in the order they're laid out, the cover story
    first, then those with a commentary, then the rest; within each, Nature and
    Science before Neuron, newest first. Opens on the newest finished week."""
    all_editions = editions(today)
    this_week = monday(today)
    finished = [e for e in all_editions if week_of(e) < this_week]
    which = which if which in all_editions else (finished or all_editions or [None])[-1]
    upcoming = {"out": (this_week + timedelta(days=7)).isoformat(),
                "so_far": Paper.query.filter(Paper.kind == "research", Paper.published >= this_week).count()}
    base = {"editions": all_editions, "next_edition": upcoming, "fetched": fetched.isoformat() if fetched else None,
            "sources": status(),
            "journals": [{"key": j.key, "name": j.name, "commentary": j.commentary} for j in JOURNALS]}
    if not which:
        return {**base, "edition": None, "papers": [], "discussed": []}
    start = week_of(which)
    papers = Paper.query.filter(Paper.kind == "research", Paper.published >= start,
                                Paper.published < start + timedelta(days=7)).all()
    notes: dict[str, list[Paper]] = {}
    for n in Paper.query.filter(Paper.kind == "commentary", Paper.on.in_([p.doi for p in papers])):
        notes.setdefault(n.on, []).append(n)

    def order(p):
        return (0 if p.cover else 1 if p.doi in notes else 2, BY_KEY[p.journal].rank, -p.published.toordinal(), p.title)

    papers.sort(key=order)
    # With no cover story and no commentary, the lead is the first paper with a figure to show.
    if papers and not papers[0].cover and papers[0].doi not in notes:
        lead = next((p for p in papers if p.figure), papers[0])
        papers.remove(lead)
        papers.insert(0, lead)
    at = all_editions.index(which)
    # Commentaries out this week on papers from earlier weeks (Neuron's Previews come with
    # its print issue, weeks after the paper went online).
    late = Paper.query.filter(Paper.kind == "commentary", Paper.published >= start,
                              Paper.published < start + timedelta(days=7), Paper.on.isnot(None)).all()
    earlier = {p.doi: p for p in Paper.query.filter(Paper.kind == "research", Paper.doi.in_([n.on for n in late]),
                                                     Paper.published < start)}
    discussed = []
    for n in late:
        if n.on in earlier:
            week = edition_id(earlier[n.on].published)
            discussed.append({**_paper(earlier[n.on], [n], lead=False),
                              "edition": week if week in all_editions else None})
    return {**base, "discussed": discussed, "edition": {"id": which, "number": int(which[6:8]), "year": int(which[:4]),
                                "start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat(),
                                "in_progress": start >= this_week,
                                "prev": all_editions[at - 1] if at else None,
                                "next": all_editions[at + 1] if at + 1 < len(all_editions) else None},
            "papers": [_paper(p, notes.get(p.doi, []), lead=i == 0) for i, p in enumerate(papers)]}


def _paper(p: Paper, notes: list[Paper], lead: bool) -> dict:
    j = BY_KEY[p.journal]
    return {"doi": p.doi, "journal": j.name, "journal_key": j.key, "title": p.title, "authors": p.authors or [],
            "abstract": p.abstract, "published": p.published.isoformat(), "url": p.url, "figure": p.figure,
            "figure_large": large(p.cover if (p.cover or "").startswith("https://") else p.figure),
            "free": p.free, "cover": bool(p.cover),
            "cover_image": p.cover if (p.cover or "").startswith("https://") else None,
            "commentary": _commentary(p, notes), "tier": "lead" if lead else "major" if notes or p.cover else "minor"}


def large(image: str | None) -> str | None:
    """The same image bigger, for the lead or an opened story (each image server has its own sizes)."""
    if not image:
        return None
    image = re.sub(r"^(https://media\.springernature\.com/)(m685|w440)/", r"\1lw1200/", image)
    return re.sub(r"(ars\.els-cdn\.com/.*-(?:ga|gr)\d+)\.jpg$", r"\1_lrg.jpg", image)


def status() -> dict:
    """Whether each journal answered on the last fetch."""
    rows = {r.key: r for r in SourceStatus.query.filter(SourceStatus.key.like("papers:%"))}
    failing = [j.name for j in JOURNALS if (r := rows.get(f"papers:{j.key}")) and r.error]
    return {"ok": len(JOURNALS) - len(failing), "total": len(JOURNALS), "failing": failing}


def last_fetched() -> datetime | None:
    last = (db.session.query(db.func.max(SourceStatus.last_ok))
            .filter(SourceStatus.key.like("papers:%"), SourceStatus.key != "papers:since").scalar())
    return last.replace(tzinfo=UTC) if last else None

