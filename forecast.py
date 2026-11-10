"""Forecast: one question about the world in each issue of Dispatch, from Polymarket.

Each issue comes out on a Wednesday (dispatch.out) and asks one yes/no question that resolves
before the next one; you answer with your chance of Yes, and the next issue says what happened,
what you said, and what the crowd said. The markets are read in polymarket.py.

Choosing (eligible and choose, pure): a yes/no market open when the issue comes out and closing
before the next, not blocked, with the crowd between 20% and 80% and at least MIN_VOLUME traded;
one market per event, the one nearest 50%. The most traded wins, never the event asked last
week; one within TIE of it is a tie, drawn at random once.

A question's state is one of STATES and moves only along MOVES, in _move and nowhere else:
none (nothing was eligible) is final; open becomes closed when trading stops; open or closed
becomes yes, no or void when Polymarket settles it. Scores and the record are worked out from
the questions and your guesses, never stored.
"""
import json
import random
from datetime import datetime, timedelta

import requests

from dispatch import latest, out, previous
from models import Article, Guess, Question, SourceStatus, db, utcnow
from polymarket import BLOCK_WORDS, MIN_VOLUME, outcome, read_events, read_market, subject, time, yes
from sources import BY_KEY
from text import cosine, tfidf, tokens

EVERY = timedelta(hours=1)          # how often open questions are checked for a result
ASK_WITHIN = timedelta(days=1)      # a question is asked as its issue comes out, or not at all

# The numbers that decide which question, with polymarket.MIN_VOLUME.
CROWD = (0.2, 0.8)                  # the crowd must be this unsure
TIE = 0.1                           # within this share of the most traded is a tie
STEP = 5                            # the slider's step, in percent

STATES = ("none", "open", "closed", "yes", "no", "void")
MOVES = {"open": {"closed", "yes", "no", "void"}, "closed": {"yes", "no", "void"}}
FINAL = set(STATES) - set(MOVES)


# ---------- the issue's week ----------

def window(edition: str) -> tuple[datetime, datetime]:
    """From when an issue comes out until the next one does: its question closes in here."""
    start = out(edition)
    return start, start + timedelta(days=7)


# ---------- choosing (pure) ----------

def _askable(m: dict, start: datetime, end: datetime) -> bool:
    """A yes/no market already trading at `start`, closing by `end`, traded enough, not blocked."""
    made, ends = time(m.get("createdAt") or m.get("startDate")), time(m.get("endDate"))
    return (json.loads(m.get("outcomes") or "[]") == ["Yes", "No"] and not m.get("closed")
            and not BLOCK_WORDS.search(m.get("question") or "") and yes(m) is not None
            and bool(made and ends) and made <= start < ends <= end
            and float(m.get("volumeNum") or 0) >= MIN_VOLUME)


def eligible(events: list[dict], start: datetime, end: datetime) -> list[dict]:
    """Every question an issue out at `start` could ask, one per event: its market nearest 50%."""
    found = []
    for e in events:
        markets = [m for m in e.get("markets") or [] if _askable(m, start, end)]
        if not subject(e) or not markets:
            continue
        m = min(markets, key=lambda m: abs(yes(m) - 0.5))
        if not CROWD[0] <= yes(m) <= CROWD[1]:
            continue
        source = m.get("resolutionSource") or e.get("resolutionSource") or ""
        found.append({"market": str(m["id"]), "event": str(e["id"]), "slug": e.get("slug"), "title": m["question"],
                      "rule": (m.get("description") or e.get("description") or "").strip(),
                      "source": source if source.startswith("https://") else None, "subject": subject(e),
                      "closes": time(m["endDate"]), "crowd": yes(m), "volume": float(m.get("volumeNum") or 0)})
    return found


def choose(candidates: list[dict], last_event: str | None, rng: random.Random) -> dict | None:
    """The most traded, never last week's event; a tie within TIE is drawn at random."""
    fresh = sorted((c for c in candidates if c["event"] != last_event), key=lambda c: -c["volume"])
    if not fresh:
        return None
    return rng.choice([c for c in fresh if c["volume"] >= (1 - TIE) * fresh[0]["volume"]])


# ---------- the reading: what the newsrooms Situation reads wrote that week ----------

# Capitalised, so tokens() counts them as names, but they name no one: "in October".
NOT_NAMES = set(tokens("x January February March April May June July August September October November December "
                       "Monday Tuesday Wednesday Thursday Friday Saturday Sunday"))


def coverage(title: str, start: datetime, limit: int = 3) -> list[dict]:
    """The articles from the week before `start` that share a name with the question (the
    News tab shows few stories; its feeds carry the rest), most alike first, one per headline."""
    articles = Article.query.filter(Article.fetched >= start - timedelta(days=7), Article.fetched < start).all()
    question = tokens(title)
    names = {t for t, n in question.items() if n >= 2} - NOT_NAMES       # tokens() counts a name twice
    docs = [tokens(a.title) for a in articles]
    vectors = tfidf([question, *docs])
    alike = sorted(((cosine(vectors[0], v), a) for v, a, d in zip(vectors[1:], articles, docs, strict=True)
                    if names & set(d)), key=lambda x: -x[0])
    out, seen = [], set()
    for _, a in alike:
        if a.title.lower() not in seen and len(out) < limit:
            seen.add(a.title.lower())
            src = BY_KEY.get(a.source)
            out.append({"title": a.title, "outlet": src.name if src else a.outlet, "url": a.url,
                        "when": (a.published or a.fetched).isoformat()})
    return out


# ---------- the only writers ----------

def answering(q: Question, now: datetime) -> bool:
    """Whether a question can still be answered: open, and not yet past its close."""
    return q.state == "open" and now < q.closes


def _move(q: Question, state: str, now: datetime):
    if state not in MOVES.get(q.state, set()):
        raise ValueError(f"a question can't go from {q.state} to {state}")
    q.state = state
    if state in FINAL:
        q.settled = now


def ask(edition: str, now: datetime, events: list[dict] | None = None, rng: random.Random | None = None) -> Question:
    """Choose an edition's question, once. With nothing eligible it's 'none', and stays so."""
    q = db.session.get(Question, edition)
    if q:
        return q
    start, end = window(edition)
    last = db.session.get(Question, previous(edition))
    pick = choose(eligible(read_events(start, end) if events is None else events, start, end),
                  last.event if last else None, rng or random.Random())
    q = Question(edition=edition, state="open" if pick else "none", asked=now, **(pick or {}))
    if pick:
        q.news = coverage(q.title, start)
    db.session.add(q)
    db.session.commit()
    return q


def settle(now: datetime) -> int:
    """Check each open or closed question with Polymarket; returns how many changed."""
    changed = 0
    for q in Question.query.filter(Question.state.in_(MOVES)).all():
        m = read_market(q.market)
        state = outcome(m) or ("closed" if q.state == "open" and (m.get("closed") or now >= q.closes) else None)
        if state:
            _move(q, state, now)
            changed += 1
    db.session.commit()
    return changed


def answer(edition: str, user: str, chance: int, now: datetime) -> Guess:
    """Save your chance of Yes (a percent), with the crowd's at this moment. Allowed while it's open."""
    q = db.session.get(Question, edition)
    if not q or not answering(q, now):
        raise ValueError("This question has closed.")
    if not 0 <= chance <= 100 or chance % STEP:
        raise ValueError(f"A chance is 0 to 100, in steps of {STEP}.")
    m = read_market(q.market)
    if m.get("closed") or yes(m) is None:
        raise ValueError("This question has closed.")
    g = db.session.get(Guess, (edition, user)) or Guess(edition=edition, user=user)
    g.chance, g.crowd, g.at = chance, yes(m), now
    db.session.add(g)
    db.session.commit()
    return g


# ---------- the job (the server's loop, hourly) ----------

def askable(edition: str, now: datetime) -> bool:
    """Whether an issue's question can still be asked: it's just come out, and it hasn't been."""
    return now < out(edition) + ASK_WITHIN and not db.session.get(Question, edition)


def due(now: datetime) -> bool:
    if askable(latest(now), now):
        return True
    status = db.session.get(SourceStatus, "forecast")
    return not status or not status.last_try or now - status.last_try >= EVERY


def run(now: datetime | None = None):
    """Ask the question of the issue just out, if it hasn't been, and look for results."""
    now = now or utcnow()
    error = None
    try:
        if askable(latest(now), now):
            ask(latest(now), now)
        settle(now)
    except (requests.RequestException, ValueError, KeyError) as e:
        db.session.rollback()
        error = type(e).__name__
    status = db.session.get(SourceStatus, "forecast") or SourceStatus(key="forecast")
    db.session.add(status)
    status.last_try, status.error = now, error
    if not error:
        status.last_ok = now
    db.session.commit()


# ---------- what the page shows ----------

def brier(chance: float, state: str) -> float:
    """The squared gap between a chance of Yes (0 to 1) and what happened: 0 is perfect."""
    return (chance - (state == "yes")) ** 2


def scores(q: Question, g: Guess) -> tuple[float, float]:
    """Your Brier score and the crowd's (at the moment you answered) on a settled question."""
    return brier(g.chance / 100, q.state), brier(g.crowd, q.state)


def _question(q: Question, g: Guess | None, now: datetime) -> dict:
    live = answering(q, now)
    out = {"edition": q.edition, "state": "closed" if q.state == "open" and not live else q.state,
           "title": q.title, "rule": q.rule, "source": q.source, "subject": q.subject,
           "closes": q.closes.isoformat(), "volume": q.volume, "news": q.news or [],
           "link": f"https://polymarket.com/event/{q.slug}", "yours": g.chance if g else None}
    # The crowd only once you've answered, or once it can't be answered: it's your judgement being kept.
    if g or not live:
        out["crowd"] = round((g.crowd if g else q.crowd) * 100)
    if g and q.state in ("yes", "no"):
        yours, crowd = scores(q, g)
        out["score"] = {"yours": round(yours, 3), "crowd": round(crowd, 3)}
    return out


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def record(user: str) -> dict:
    """How you've done against the crowd, overall and by subject, and how often what you said
    came true, in bands of 20 points."""
    rows = (db.session.query(Question, Guess).join(Guess, Guess.edition == Question.edition)
            .filter(Guess.user == user, Question.state.in_(("yes", "no"))).all())
    scored = [(q, g, *scores(q, g)) for q, g in rows]
    subjects: dict[str, list] = {}
    for q, _, yours, crowd in scored:
        subjects.setdefault(q.subject, []).append((yours, crowd))
    bands = [{"from": lo, "to": lo + 20, "said": 0, "happened": 0} for lo in range(0, 100, 20)]
    for q, g, _, _ in scored:
        band = bands[min(g.chance // 20, 4)]
        band["said"] += 1
        band["happened"] += q.state == "yes"
    return {"answered": len(scored),
            "yours": _mean([s[2] for s in scored]), "crowd": _mean([s[3] for s in scored]),
            "subjects": [{"subject": s, "answered": len(v), "yours": _mean([a for a, _ in v]),
                          "crowd": _mean([b for _, b in v])}
                         for s, v in sorted(subjects.items(), key=lambda kv: -len(kv[1]))],
            "bands": bands}


def view(edition: str | None, user: str, now: datetime) -> dict | None:
    """An edition's forecast: its question, the results it reports (last week's, and any older
    one still waiting when last week's edition came out), and your record. None until it's out."""
    if not edition or out(edition) > now:
        return None
    q = db.session.get(Question, edition)
    guesses = {g.edition: g for g in Guess.query.filter_by(user=user)}
    before = previous(edition)
    report = [p for p in Question.query.filter(Question.edition < edition, Question.state != "none")
              .order_by(Question.edition.desc())
              if p.edition == before or not p.settled or p.settled >= out(before)]
    return {"question": _question(q, guesses.get(edition), now) if q and q.state != "none" else None,
            "asked": q is not None, "pending": askable(edition, now),
            "results": [_question(p, guesses.get(p.edition), now) for p in report],
            "record": record(user)}
