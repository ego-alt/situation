"""Papers: what the journals in journals.py made more of, for Dispatch's front page.

Once a day, as an issue is made up, the last three weeks of each journal are read from
Europe PMC: abstracts, article types, free copies and PubMed's commentary links. A paper is
worth an issue's space when its journal made more of it: put it on the cover, or commissioned
a commentary on it. Each runs once (take), a few a day, the longest out first, so a journal's
weekly batch lasts until its next.
"""
import html
import math
import re
from datetime import UTC, date, datetime, timedelta

import feedparser
import requests

from journals import BY_KEY, FIELDS, JOURNALS, Journal
from models import Paper, SourceStatus, db, utcnow
from pipeline import record_sources
from sources import USER_AGENT

EUROPE_PMC = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
CROSSREF = "https://api.crossref.org/works/"
WINDOW = timedelta(days=21)     # commentaries and free copies come later, so look back this far
KEEP = WINDOW + timedelta(days=7)   # how long what's read is remembered: past the window, or it's read as new again
MOST = 3                        # the most papers in one issue
SPREAD = 3                      # the days the papers waiting are spread over
EARLIER = timedelta(days=7)     # a paper out longer ago than this is dated
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


def paragraphs(parts) -> str:
    """Pieces of a page's text as one: a blank line between paragraphs, a <br> a new line. Links,
    spans and a link's icon dissolve first (clean leaves a space where a tag was)."""
    inline = re.compile(r"<svg\b.*?</svg>|</?(?:a|span|strong|b|u)\b[^>]*>", re.S)
    lines = (re.split(r"<br\s*/?>", inline.sub("", part or "")) for part in parts)
    return "\n\n".join(filter(None, ("\n".join(filter(None, map(clean, part))) for part in lines)))


def in_a_field(title: str, abstract: str) -> bool:
    """The "words" scope: a word of one of FIELDS in the title, or two different ones of one in the abstract."""
    title, abstract = plain(title), plain(abstract)
    return any(words.search(title) or len({m.lower() for m in words.findall(abstract)}) >= 2
               for words in FIELDS.values())


# ---------- what Europe PMC says ----------

def europe_pmc(issn: str, since: date, until: date) -> list[dict]:
    """Everything of a journal's that Europe PMC took in between two days (by the day it came in:
    a piece only in print is dated the 1st of its month)."""
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
    """research, commentary, news (where Nature files News & Views) or other."""
    types = set(record.get("pubTypeList", {}).get("pubType", []))
    if "Comment" in types:
        return "commentary"
    if "News" in types:
        return "news"
    if types & NOT_RESEARCH:
        return "other"
    if "research-article" in types or len(record.get("abstractText") or "") >= 400:
        return "research"
    return "other"   # front matter


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
    """The day it came out, online or in print; a print date that's only a month gives
    way to the day Europe PMC got it."""
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
    """The DOIs in one of a publisher's subject feeds (Nature's)."""
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
    img = re.search(r'<img\s+src="([^"]+)"[^>]*data-test="issue-cover-image"', text)
    return (m.group(1).lower(), img.group(1) if img else None) if m else None


def references(doi: str) -> list[str]:
    """The DOIs a piece cites, from Crossref."""
    refs = get(CROSSREF + doi).json()["message"].get("reference", [])
    return [r["DOI"].lower() for r in refs if r.get("DOI")]


def figure_for(doi: str) -> str | None:
    """The graphical abstract or first figure: Cell Press's from Elsevier's image server,
    others' from the og:image on the paper's page."""
    if doi.startswith("10.1016/"):
        ids = get(CROSSREF + doi).json()["message"].get("alternative-id", [])
        pii = next((i for i in ids if re.fullmatch(r"S\w{16}", i)), None)
        urls = [f"https://ars.els-cdn.com/content/image/1-s2.0-{pii}-{n}.jpg" for n in ("ga1", "gr1")] if pii else []
        return next((u for u in urls if requests.head(u, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}).ok), None)
    try:
        image = page_meta(f"https://doi.org/{doi}").get("og:image", "")
    except requests.HTTPError:
        return None         # Science turns readers like this away
    return image if re.match(r"https://", image) and re.search(r"Fig|fig|MediaObjects", image) else None


# ---------- a fetch ----------

def fetch(now: datetime | None = None) -> dict:
    """Read every journal for the last WINDOW and forget what's older than KEEP; returns
    {status key: (papers, error)}."""
    now = now or datetime.now(UTC)
    since, until = (now - WINDOW).date(), now.date()
    report = {}
    for j in JOURNALS:
        try:
            report[f"papers:{j.key}"] = (_read(j, since, until), None)
        except (requests.RequestException, ValueError, KeyError) as e:
            db.session.rollback()
            report[f"papers:{j.key}"] = (0, type(e).__name__)
    record_sources(report, now)
    _link_commentaries(since)
    _covers()
    _enrich()
    prune(now - KEEP)
    db.session.commit()
    return report


def _read(j, since: date, until: date) -> int:
    """Store a journal's papers in scope and its commentaries; returns how many papers."""
    known = {p.doi: p for p in Paper.query.filter(Paper.journal == j.key, Paper.published >= since)}
    in_subject = set().union(*map(subject_dois, j.subject_feeds))
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
            if j.scope == "words" and not in_a_field(title, abstract):
                continue
            count += 1
        elif kind == "news":
            # Nature's News & Views are filed as news: its page says which it is.
            if not j.commentary_page_type or doi in known or doi not in in_subject:
                continue
            page_type = page_meta(f"https://doi.org/{doi}").get("dc.type")
            kind = "commentary" if page_type == j.commentary_page_type else "other"
            if kind == "other":
                known[doi] = Paper(doi=doi, journal=j.key, kind="other", title=title, published=published)
                db.session.add(known[doi])
                db.session.commit()
                continue
        elif kind != "commentary":
            continue
        known[doi] = _store(j, r, kind, known.get(doi))
        db.session.commit()     # each as it comes, so the database isn't held while pages are read
    return count


def _store(j, r: dict, kind: str, p: Paper | None = None) -> Paper:
    """Save what Europe PMC says about a research paper or commentary."""
    doi = r["doi"].lower()
    p = p or db.session.get(Paper, doi) or Paper(doi=doi, journal=j.key, kind=kind)
    if p.kind == "other":
        return p
    p.title, p.published = clean(r.get("title")).rstrip("."), published_on(r)
    p.authors, p.url = surnames(r), f"https://doi.org/{doi}"
    p.abstract = clean(r.get("abstractText")) or p.abstract   # a commentary's is its summary
    if kind == "research":
        p.free = free_copy(r)
    else:
        on = _links(r, "Comment on")
        p.on = p.on or (on[0] if on else None)
    db.session.add(p)
    return p


def _adopt(doi: str, j: Journal) -> bool:
    """Add an older paper of journal `j` that a commentary there is about, if it's in scope here."""
    found = get(EUROPE_PMC, query=f'DOI:"{doi}"', format="json", resultType="core").json()
    r = next(iter(found.get("resultList", {}).get("result", [])), None)
    info = (r or {}).get("journalInfo", {}).get("journal", {})
    if not (r and j.issn in (info.get("issn"), info.get("essn")) and published_on(r) and kind_of(r) == "research"):
        return False
    if j.scope == "words" and not in_a_field(r.get("title", ""), r.get("abstractText", "")):
        return False
    _store(j, r, "research")
    return True


def _link_commentaries(since: date):
    """Tie each commentary to its paper, one of its own journal's: PubMed's link, else the first
    of its first few references that's one here (an older one is added)."""
    research = {(j, d) for j, d in db.session.query(Paper.journal, Paper.doi).filter(Paper.kind == "research")}
    for c in Paper.query.filter(Paper.kind == "commentary", Paper.published >= since).all():
        if (c.journal, c.on) in research:
            continue
        try:
            for doi in [c.on] if c.on else references(c.doi)[:3]:
                if (c.journal, doi) in research or _adopt(doi, BY_KEY[c.journal]):
                    c.on = doi
                    research.add((c.journal, doi))
                    break
        except (requests.RequestException, ValueError, KeyError):
            continue
        db.session.commit()


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
            p.cover = found[1] or "yes"
    db.session.commit()


def _enrich(limit: int = 60):
    """Look once for the figure of each paper that will run, and for the summary of a commentary
    Europe PMC has none for (Nature's News & Views: its page's description)."""
    discussed = db.session.query(Paper.on).filter(Paper.kind == "commentary", Paper.on.isnot(None))
    runs = db.and_(Paper.kind == "research", db.or_(Paper.cover.isnot(None), Paper.doi.in_(discussed)))
    new = Paper.query.filter(db.or_(runs, Paper.kind == "commentary"), Paper.enriched.is_(None)).limit(limit).all()
    for p in new:
        try:
            if p.kind == "research":
                p.figure = figure_for(p.doi)
            elif not p.abstract and BY_KEY[p.journal].commentary_page_type:
                p.abstract = clean(page_meta(p.url).get("description"))
        except (requests.RequestException, ValueError, KeyError):
            continue        # tried again next time
        p.enriched = utcnow()
        db.session.commit()


def prune(cutoff: datetime) -> int:
    """Forget what was first read before `cutoff`. A paper stays while a commentary on it does."""
    discussed = db.session.query(Paper.on).filter(Paper.on.isnot(None), Paper.fetched >= cutoff)
    return Paper.query.filter(Paper.fetched < cutoff, Paper.doi.notin_(discussed)).delete(synchronize_session=False)


def read_since(when: datetime) -> bool:
    """Whether a journal has answered since `when`."""
    return SourceStatus.query.filter(SourceStatus.key.like("papers:%"), SourceStatus.last_ok >= when).count() > 0


# ---------- for the issue (dispatch.py) ----------

def story(id: str, source: str, title: str, url: str, published: date, **more) -> dict:
    """A story on the front page, whatever it's from (features.py makes them too)."""
    return {"id": id, "source": source, "title": title, "url": url, "published": published.isoformat(),
            "authors": [], "abstract": "", "figure": None, "figure_large": None, "free": None, "cover": False,
            "cover_image": None, "commentary": [], "earlier": False, **more}


def take(edition: str, day: date) -> list[dict]:
    """The papers for the issue out on `day`, in layout order (the cover story, then by journal
    rank), each marked as run in it. Waiting are a cover story and any paper with a commentary
    that hasn't run; the cover story and the longest out go first, a SPREADth of them a day."""
    notes: dict[str, list[Paper]] = {}
    for n in Paper.query.filter(Paper.kind == "commentary", Paper.on.isnot(None)):
        notes.setdefault(n.on, []).append(n)
    waiting = Paper.query.filter(Paper.kind == "research", Paper.ran.is_(None),
                                 db.or_(Paper.cover.isnot(None), Paper.doi.in_(list(notes)))).all()
    waiting.sort(key=lambda p: (not p.cover, p.published, BY_KEY[p.journal].rank, p.title))
    taken = waiting[:min(MOST, math.ceil(len(waiting) / SPREAD))]
    for p in taken:
        p.ran = edition
    taken.sort(key=lambda p: (not p.cover, BY_KEY[p.journal].rank))
    return [_story(p, notes.get(p.doi, []), day) for p in taken]


def _story(p: Paper, notes: list[Paper], day: date) -> dict:
    return story(p.doi, BY_KEY[p.journal].name, p.title, p.url, p.published, authors=p.authors or [],
                 abstract=p.abstract, figure=p.figure, figure_large=large(p.figure), free=p.free,
                 cover=bool(p.cover), cover_image=p.cover if (p.cover or "").startswith("https://") else None,
                 earlier=p.published < day - EARLIER,
                 commentary=[{"title": n.title, "url": n.url, "label": BY_KEY[n.journal].commentary,
                              "summary": n.abstract} for n in notes])


def large(image: str | None) -> str | None:
    """The same image bigger, for the lead or an opened story."""
    if not image:
        return None
    image = re.sub(r"^(https://media\.springernature\.com/)(m685|w440)/", r"\1lw1200/", image)
    return re.sub(r"(ars\.els-cdn\.com/.*-(?:ga|gr)\d+)\.jpg$", r"\1_lrg.jpg", image)
