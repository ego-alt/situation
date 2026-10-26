"""Label one week of news into events with Claude, for the ground truth (evaluation/groundtruth.py).

    sqlite3 ~/Projects/situation/instance/situation.db ".backup /tmp/situation.db"   # on the Pi
    scp pi:/tmp/situation.db /tmp/situation.db
    uv run --with anthropic python -I tools/label_week.py /tmp/situation.db --dry-run
    uv run --with anthropic python -I tools/label_week.py /tmp/situation.db             # last full week
    uv run --with anthropic python -I tools/label_week.py /tmp/situation.db --week 2026-W44
    scp instance/groundtruth/2026-W44.json.gz pi:Projects/situation/instance/groundtruth/   # backed up there

It cuts two 48-hour windows from a copy of the database (never the live one):
Monday to Wednesday morning and Friday to Sunday morning (UTC), a weekday and
a weekend, every article in them. Claude labels two things, in one batch:

  news    each article news or not (NEWS_RUBRIC), 40 at a time: the truth
          for stage 1
  events  which articles report one event (RUBRIC), bundle by bundle: the
          truth for stage 2 (over the articles that are news). Bundles are
          articles loosely alike, so every event falls inside one; articles
          in no bundle are one-article events
Claude labels the bundles through the Message Batches API (half the price,
usually within the hour; --direct asks one bundle at a time). The week is
written to the ground truth folder, and each window's review is printed:
check it before trusting the week. Needs ANTHROPIC_API_KEY; if the wait is
interrupted, run it again with --resume.
"""
import argparse
import json
import pathlib
import sqlite3
import sys
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import embed  # noqa: E402
import text  # noqa: E402
from evaluation import groundtruth  # noqa: E402
from geo import _table  # noqa: E402
from labels import _name, read_snapshot  # noqa: E402

MODEL = "claude-sonnet-5-5"
WINDOW = timedelta(hours=48)
ENDS = ((3, 3), (7, 3))   # windows end Wednesday and Sunday at 03:00 UTC (ISO weekday 1 = Monday, hour)
BUNDLE = 0.15             # word likeness that puts two articles in one bundle
MEANING = 0.55            # ...or meaning likeness, with the embedding model
MAX_BUNDLE = 80           # bigger bundles are cut again, a little stricter

RUBRIC = """You label news for a live "monitor the situation" dashboard. Sort the articles into events.

An event is one thing that happened, as a front page would run it as one headline: a fire at an army base; a \
referendum result; a US envoy meeting Putin. Reports of the same thing from different outlets, and its follow-ups \
within these two days (the death toll rising, arrests after it, officials responding to it), are the same event. \
A different thing happening is a different event, even within the same war, campaign or crisis: the envoy's \
meeting with Putin and a battlefield offensive are two events; two separate attacks are two events. Every article \
goes in exactly one event; an event can be a single article.

For each event, say where it happens: the ISO 3166-1 alpha-2 code of the country where it takes place (where a \
meeting or summit is held, where an attack lands), not of those commenting from afar. Hong Kong is HK, Taiwan TW, \
the Palestinian territories PS, Kosovo XK. Use "none" when it doesn't happen in one country (world markets, a \
global study).

Answer only by calling record_stories, once, with every article in it."""

TOOL = {
    "name": "record_stories",
    "description": "Record how the articles split into events, and where each happens.",
    "input_schema": {
        "type": "object",
        "properties": {"stories": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "articles": {"type": "array", "items": {"type": "integer"},
                             "description": "The numbers of the articles in this story."},
                "place": {"type": "string", "description": "ISO 3166-1 alpha-2 code, or \"none\"."},
            },
            "required": ["articles", "place"],
        }}},
        "required": ["stories"],
    },
}


NEWS_RUBRIC = """You label articles for a live "monitor the situation" news dashboard. For each one, say whether it \
is NEWS or NOT.

NEWS reports something that happened or is happening: an event, a development, a decision, a statement, an \
incident, a result. Live blogs of unfolding events are news. So are: previews of a scheduled event that report where \
things stand ("Brazil gets ready for Sunday's knife-edge election"); pieces about the people in a current event that \
report new facts or statements ("Barber who tackled the MP's attacker praised by PM"); and lighter or slower news, \
when something happened (an opening, a restoration, a market move). Sport and entertainment results are news. \
Whether any of it matters is decided elsewhere.

NOT news: opinion, columns, editorials and letters; analysis and commentary, even when it's about today's events \
("What progress after the envoy met Zelensky?", "Why Zelensky took the gloves off"); explainers, Q&As and "what we \
know"; features, profiles, interviews and long reads that report nothing new; look-backs and anniversaries; \
digests and round-ups of several stories ("... and other Mideast news"), briefings, newsletters and summaries; \
reviews, guides, galleries, quizzes and puzzles; video-only items (a page that is a video, not a written report); \
and pages that aren't articles (topic pages, scores, TV bulletins).

Each article comes with its outlet, the path of its web address (often a clue: /opinion/, /video/, /features/), \
its headline and the start of its summary. Answer only by calling record_news, once, with every number in exactly \
one list."""

NEWS_TOOL = {
    "name": "record_news",
    "description": "Record which articles are news and which aren't.",
    "input_schema": {"type": "object", "properties": {
        "news": {"type": "array", "items": {"type": "integer"}},
        "not_news": {"type": "array", "items": {"type": "integer"}}}, "required": ["news", "not_news"]},
}
NEWS_CHUNK = 40          # articles per news request


# The week's windows, from the database -------------------------------------------

def last_full_week(today: date | None = None) -> str:
    year, week, _ = ((today or datetime.now(UTC).date()) - timedelta(days=7)).isocalendar()
    return f"{year}-W{week:02d}"


def windows(db_path: str, week: str) -> dict[str, dict]:
    """{name: window} for the week, as labels.save_snapshot writes them."""
    year, number = (int(x) for x in week.split("-W"))
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT source, title, url, summary, position, coalesce(published, fetched) AS at "
                       "FROM articles WHERE coalesce(published, fetched) IS NOT NULL").fetchall()
    itn = con.execute("SELECT fetched, items, ongoing FROM itn_snapshots ORDER BY fetched").fetchall()
    con.close()
    when = lambda text: datetime.fromisoformat(text).replace(tzinfo=UTC)  # noqa: E731
    articles = [(when(r["at"]), r) for r in rows]
    out = {}
    for weekday, hour in ENDS:
        taken = datetime.fromisocalendar(year, number, weekday).replace(hour=hour, tzinfo=UTC)
        items = [{"source": r["source"], "title": r["title"], "url": r["url"], "summary": r["summary"],
                  "position": r["position"], "published": t.isoformat()}
                 for t, r in articles if taken - WINDOW < t <= taken]
        before = [x for x in itn if when(x["fetched"]) <= taken]
        last = before[-1] if before else None
        if len(items) >= 20:
            out[taken.strftime("%Y-%m-%dT%H%M")] = {
                "taken": taken.isoformat(), "items": items,
                "itn": {"items": json.loads(last["items"]), "ongoing": json.loads(last["ongoing"])}
                if last else {"items": [], "ongoing": []}}
    return out


def bundles(items: list[dict], threshold: float = BUNDLE) -> list[list[int]]:
    """Indexes of articles loosely alike, so that a story never spans two bundles."""
    vectors = text.tfidf([text.document(a) for a in items])
    meaning = embed.encode([embed.text(a) for a in items]) if embed.available() else None
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    index: dict[str, list[int]] = {}
    for i, v in enumerate(vectors):
        for t in v:
            index.setdefault(t, []).append(i)
    for i, v in enumerate(vectors):
        for j in {j for t in v for j in index[t] if j > i}:
            if text.cosine(v, vectors[j]) >= threshold:
                parent[find(i)] = find(j)
    if meaning is not None:
        import numpy as np
        close = np.triu(meaning @ meaning.T, 1) >= MEANING + (threshold - BUNDLE)
        for i, j in zip(*np.nonzero(close), strict=True):
            parent[find(int(i))] = find(int(j))
    groups: dict[int, list[int]] = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for g in groups.values():
        if len(g) > MAX_BUNDLE and threshold < 0.6:
            out += [[g[i] for i in part] for part in bundles([items[i] for i in g], threshold + 0.05)]
        elif len(g) >= 2:
            out.append(g)
    return out


# Asking Claude --------------------------------------------------------------------

def prompt(articles: list[dict]) -> str:
    lines = []
    for n, a in enumerate(articles, 1):
        summary = " ".join((a.get("summary") or "").split()[:35])
        when = a["published"].strftime("%a %d %b %H:%M UTC") if a.get("published") else ""
        lines.append(f"[{n}] {_name(a)} · {when}\n{a['title']}" + (f"\n{summary}" if summary else ""))
    return "Articles:\n\n" + "\n\n".join(lines)


def request(articles: list[dict], model: str) -> dict:
    return {"model": model, "max_tokens": 2000, "system": RUBRIC, "tools": [TOOL],
            "tool_choice": {"type": "auto"},          # newer models don't take a forced tool; the rubric asks for it
            "messages": [{"role": "user", "content": prompt(articles)}]}


def _country(code) -> str | None:
    code = str(code or "").strip()
    return "none" if code.lower() == "none" else code.upper() if code.upper() in _table()[0] else None


def parse(answer: dict, count: int) -> tuple[list[list[int]], list[str | None]] | None:
    """(groups of article indexes, where each happens), or None if the answer
    doesn't add up (an article left out or in two events)."""
    groups, places, seen = [], [], set()
    for story in answer.get("stories", []):
        members = [n - 1 for n in story.get("articles", []) if isinstance(n, int) and 1 <= n <= count]
        if not members or seen & set(members):
            return None
        seen |= set(members)
        groups.append(members)
        places.append(_country(story.get("place")))
    if len(seen) != count:
        return None
    return groups, places


def news_prompt(articles: list[dict]) -> str:
    lines = []
    for n, a in enumerate(articles, 1):
        path = urlparse(a["url"]).path if "news.google.com" not in a["url"] else "(wire, via Google News)"
        summary = " ".join((a.get("summary") or "").split()[:30])
        lines.append(f"[{n}] {_name(a)} · {path}\n{a['title']}" + (f"\n{summary}" if summary else ""))
    return "Articles:\n\n" + "\n\n".join(lines)


def news_request(articles: list[dict], model: str) -> dict:
    return {"model": model, "max_tokens": 1000, "system": NEWS_RUBRIC, "tools": [NEWS_TOOL],
            "tool_choice": {"type": "auto"}, "messages": [{"role": "user", "content": news_prompt(articles)}]}


def parse_news(answer: dict, count: int) -> list[bool] | None:
    """Each article's news-or-not, or None unless every article is in exactly one list."""
    news = {n - 1 for n in answer.get("news", []) if isinstance(n, int) and 1 <= n <= count}
    other = {n - 1 for n in answer.get("not_news", []) if isinstance(n, int) and 1 <= n <= count}
    if news & other or len(news | other) != count:
        return None
    return [k in news for k in range(count)]


def answer_of(message) -> dict | None:
    for block in message.content:
        if block.type == "tool_use" and block.name in ("record_stories", "record_news"):
            return block.input
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("db", help="a copy of instance/situation.db")
    p.add_argument("--week", default=None, help="the ISO week to label, e.g. 2026-W44 (default: the last full one)")
    p.add_argument("--dir", default=None, help="the ground truth folder (default: groundtruth.directory())")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--limit", type=int, default=None, help="at most this many bundles (a trial)")
    p.add_argument("--dry-run", action="store_true", help="count the work and the tokens; ask nothing")
    p.add_argument("--direct", action="store_true", help="one request at a time instead of a batch")
    p.add_argument("--resume", action="store_true", help="collect a batch this script submitted earlier")
    args = p.parse_args()
    week = args.week or last_full_week()
    state_path = groundtruth.directory(args.dir) / f".{week}.state.json"

    if args.resume:
        state = json.loads(state_path.read_text())
        wins, work, model = state["windows"], state["work"], state["model"]
    else:
        model = args.model
        wins = windows(args.db, week)
        work = []
        for name, win in wins.items():
            items, _, _ = read_snapshot(win)
            urls = [a["url"] for a in items]
            work += [{"id": f"n-{name}-{k}", "kind": "news", "window": name, "urls": urls[k:k + NEWS_CHUNK]}
                     for k in range(0, len(urls), NEWS_CHUNK)]
            work += [{"id": f"e-{name}-{k}", "kind": "events", "window": name, "urls": [urls[i] for i in b]}
                     for k, b in enumerate(bundles(items))]
        work = work[:args.limit] if args.limit else work
    articles_of = {name: {a["url"]: a for a in read_snapshot(win)[0]} for name, win in wins.items()}
    jobs = {w["id"]: (w["kind"], w["window"], [articles_of[w["window"]][u] for u in w["urls"]]) for w in work}
    requests = {key: (news_request if kind == "news" else request)(arts, model)
                for key, (kind, _, arts) in jobs.items()}
    # Measured: tool use adds a system prompt of its own, about 500 tokens a call.
    tokens = sum(len(r["system"]) + len(json.dumps(r["tools"])) + len(r["messages"][0]["content"])
                 for r in requests.values()) / 3.5 + 500 * len(requests)
    total = sum(len(a) for a in articles_of.values())
    kinds = [k for k, _, _ in jobs.values()]
    print(f"{week}: {len(wins)} windows, {total} articles; {kinds.count('news')} news requests and "
          f"{kinds.count('events')} event bundles; about {tokens / 1e6:.2f}M input tokens and "
          f"{len(jobs) * 150 / 1e6:.2f}M output, with {model}")
    if args.dry_run or not jobs:
        return

    import anthropic
    client = anthropic.Anthropic()
    answers: dict[str, dict | None] = {}
    if args.direct:
        for n, (key, req) in enumerate(requests.items(), 1):
            answers[key] = answer_of(client.messages.create(**req))
            print(f"\r{n}/{len(requests)}", end="", flush=True)
        print()
    else:
        if args.resume:
            batch_id = state["batch"]
        else:
            batch_id = client.messages.batches.create(requests=[
                {"custom_id": key, "params": req} for key, req in requests.items()]).id
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps({"batch": batch_id, "model": model, "windows": wins, "work": work}))
            print(f"submitted batch {batch_id}; waiting (Ctrl-C is safe: run again with --resume)")
        while client.messages.batches.retrieve(batch_id).processing_status != "ended":
            time.sleep(60)
        for result in client.messages.batches.results(batch_id):
            answers[result.custom_id] = answer_of(result.result.message) if result.result.type == "succeeded" else None

    # Each window's news labels, and its events: Claude's, then its articles in
    # no bundle, one each. A window with any answer that doesn't add up is left out.
    news: dict[str, dict[str, bool]] = {name: {} for name in wins}
    events: dict[str, list[dict]] = {name: [] for name in wins}
    bundled: dict[str, set] = {name: set() for name in wins}
    bad = set()
    for key, (kind, name, arts) in jobs.items():
        if kind == "news":
            labels = parse_news(answers.get(key) or {}, len(arts))
            if labels is None:
                bad.add(name)
                continue
            news[name] |= {a["url"]: is_news for a, is_news in zip(arts, labels, strict=True)}
        else:
            bundled[name] |= {a["url"] for a in arts}
            parsed = parse(answers.get(key) or {}, len(arts))
            if parsed is None:
                bad.add(name)
                continue
            events[name] += [{"urls": [arts[i]["url"] for i in g], "place": place}
                             for g, place in zip(*parsed, strict=True)]
    for name in wins:
        events[name] += [{"urls": [u], "place": None} for u in articles_of[name] if u not in bundled[name]]
        if len(news[name]) != len(articles_of[name]):
            bad.add(name)
    if bad:
        print(f"answers that didn't add up: windows {', '.join(sorted(bad))} are left out")
    keep = [n for n in wins if n not in bad]
    path = groundtruth.save(week, {"made": datetime.now(UTC).isoformat(), "model": model,
                                   "rubric": RUBRIC, "news_rubric": NEWS_RUBRIC,
                                   "windows": {n: wins[n] for n in keep}, "news": {n: news[n] for n in keep},
                                   "events": {n: events[n] for n in keep}}, args.dir)
    print(f"wrote {path}")
    for w in groundtruth.load(week, args.dir):
        print(groundtruth.describe(w))
    state_path.unlink(missing_ok=True)

if __name__ == "__main__":
    main()
