"""Claude labels whole snapshots into events, and where each happens.

An event is one thing that happened, as a front page would run it as one
headline (see RUBRIC). Which articles report the same event has an answer a
careful reader would agree on, so Claude labels it in bulk; the labels are the
evaluation set for grouping (tools/fast_win.py). Only news is labelled
(score.is_news): opinion, explainers and the like are left out, as the app
leaves them out.

    sqlite3 ~/Projects/situation/instance/situation.db ".backup /tmp/situation.db"   # on the Pi
    scp pi:/tmp/situation.db /tmp/situation.db
    uv run --with anthropic python -I tools/claude_label.py /tmp/situation.db labels.json.gz --dry-run
    uv run --with anthropic python -I tools/claude_label.py /tmp/situation.db labels.json.gz --direct --limit 10
    uv run --with anthropic python -I tools/claude_label.py /tmp/situation.db labels.json.gz
    scp labels.json.gz pi:Projects/situation/instance/
    ssh pi docker exec situation flask import-labels instance/labels.json.gz

It reads a copy of the database (never the live one), cuts a snapshot every
48 hours from the articles it holds (the window the app groups), and splits
each snapshot into bundles: articles loosely alike, so every event falls
inside one bundle and Claude sees each bundle whole. Articles in no bundle are
labelled as one-article events (the bundling is loose enough that a related
article would have joined one). The run goes through the Message Batches API
(half the price; usually done within the hour); --direct asks one bundle at a
time instead, for a quick look. Needs ANTHROPIC_API_KEY. If the wait is
interrupted, run it again with --resume to collect the batch.
"""
import argparse
import gzip
import json
import pathlib
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import cluster  # noqa: E402
import embed  # noqa: E402
from geo import _table  # noqa: E402
from labels import _name, read_snapshot  # noqa: E402
from score import news_only  # noqa: E402

MODEL = "claude-sonnet-5-5"
WINDOW = timedelta(hours=48)
BUNDLE = 0.15             # word likeness that puts two articles in one bundle
MEANING = 0.55            # ...or meaning likeness, with the embedding model
MAX_BUNDLE = 80           # bigger bundles are cut again, a little stricter (40 split running stories)

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


# Snapshots from the database -------------------------------------------------

def snapshots(db_path: str, every: timedelta = WINDOW, limit: int | None = None) -> dict[str, dict]:
    """{name: snapshot}, one per window back from the newest article, as labels.save_snapshot writes them."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT source, title, url, summary, position, coalesce(published, fetched) AS at "
                       "FROM articles WHERE coalesce(published, fetched) IS NOT NULL").fetchall()
    itn = con.execute("SELECT fetched, items, ongoing FROM itn_snapshots ORDER BY fetched").fetchall()
    con.close()
    when = lambda text: datetime.fromisoformat(text).replace(tzinfo=UTC)  # noqa: E731
    articles = [(when(r["at"]), r) for r in rows]
    if not articles:
        return {}
    newest = max(t for t, _ in articles)
    oldest = min(t for t, _ in articles)
    out = {}
    taken = newest.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while taken > oldest and (limit is None or len(out) < limit):
        items = news_only([{"source": r["source"], "title": r["title"], "url": r["url"], "summary": r["summary"],
                            "position": r["position"], "published": t.isoformat()}
                           for t, r in articles if taken - WINDOW < t <= taken])
        before = [x for x in itn if when(x["fetched"]) <= taken]
        last = before[-1] if before else None
        if len(items) >= 20:
            name = taken.strftime("%Y-%m-%dT%H%M")
            out[name] = {"taken": taken.isoformat(), "items": items,
                         "itn": {"items": json.loads(last["items"]), "ongoing": json.loads(last["ongoing"])}
                         if last else {"items": [], "ongoing": []}}
        taken -= every
    return out


def bundles(items: list[dict], threshold: float = BUNDLE) -> list[list[int]]:
    """Indexes of articles loosely alike, so that a story never spans two bundles."""
    vectors = cluster.tfidf([cluster.document(a) for a in items])
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
            if cluster.cosine(v, vectors[j]) >= threshold:
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


def label(name: str, urls: list[str], groups: list[list[str]], places: list, model: str) -> dict:
    return {"kind": "grouping", "snapshot": name, "source": f"claude:{model}",
            "a": {"bundle": urls, "groups": groups}, "b": {"places": places}}


def answer_of(message) -> dict | None:
    for block in message.content:
        if block.type == "tool_use" and block.name == "record_stories":
            return block.input
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("db", help="a copy of instance/situation.db")
    p.add_argument("out", help="where to write the labels (.json.gz)")
    p.add_argument("--model", default=MODEL)
    p.add_argument("--snapshots", type=int, default=None, help="at most this many snapshots, newest first")
    p.add_argument("--limit", type=int, default=None, help="at most this many bundles")
    p.add_argument("--skip", type=int, default=0, help="leave out the first this many bundles")
    p.add_argument("--dry-run", action="store_true", help="count the work and the tokens; ask nothing")
    p.add_argument("--direct", action="store_true", help="one request at a time instead of a batch")
    p.add_argument("--resume", action="store_true", help="collect a batch this script submitted earlier")
    args = p.parse_args()

    state_path = pathlib.Path(args.out + ".state.json")
    if args.resume:
        state = json.loads(state_path.read_text())
        snaps, work, model = state["snapshots"], state["work"], state["model"]
    else:
        model = args.model
        snaps = snapshots(args.db, limit=args.snapshots)
        work = []
        for name, snap in snaps.items():
            items, _, _ = read_snapshot(snap)
            for k, b in enumerate(bundles(items)):
                work.append({"id": f"{name}-{k}", "snapshot": name, "urls": [items[i]["url"] for i in b]})
        work = work[args.skip:args.skip + args.limit] if args.limit else work[args.skip:]
    articles_of = {}
    for name, snap in snaps.items():
        items, _, _ = read_snapshot(snap)
        articles_of[name] = {a["url"]: a for a in items}
    jobs = {w["id"]: (w["snapshot"], [articles_of[w["snapshot"]][u] for u in w["urls"]]) for w in work}

    # Measured on a sample: tool use adds a system prompt of its own, about 500 tokens a call.
    tokens_in = sum(len(RUBRIC) + len(json.dumps(TOOL)) + len(prompt(arts)) for _, arts in jobs.values()) / 3.5
    tokens_in += 500 * len(jobs)
    print(f"{len(snaps)} snapshots, {len(jobs)} bundles, {sum(len(a) for _, a in jobs.values())} articles; "
          f"about {tokens_in / 1e6:.2f}M input tokens and {len(jobs) * 150 / 1e6:.2f}M output, with {model}")
    if args.dry_run:
        return

    import anthropic
    client = anthropic.Anthropic()
    answers: dict[str, dict | None] = {}
    if args.direct:
        used_in = used_out = 0
        for n, (key, (_, arts)) in enumerate(jobs.items(), 1):
            message = client.messages.create(**request(arts, model))
            answers[key] = answer_of(message)
            used_in, used_out = used_in + message.usage.input_tokens, used_out + message.usage.output_tokens
            print(f"\r{n}/{len(jobs)}", end="", flush=True)
        print(f"\ntokens used: {used_in} in, {used_out} out")
    else:
        if args.resume:
            batch_id = state["batch"]
        else:
            batch = client.messages.batches.create(requests=[
                {"custom_id": key, "params": request(arts, model)} for key, (_, arts) in jobs.items()])
            batch_id = batch.id
            state_path.write_text(json.dumps({"batch": batch_id, "model": model, "snapshots": snaps, "work": work}))
            print(f"submitted batch {batch_id}; waiting (Ctrl-C is safe: run again with --resume)")
        while (batch := client.messages.batches.retrieve(batch_id)).processing_status != "ended":
            c = batch.request_counts
            print(f"\r{c.succeeded + c.errored} of {len(jobs)} done", end="", flush=True)
            time.sleep(60)
        print()
        for result in client.messages.batches.results(batch_id):
            answers[result.custom_id] = answer_of(result.result.message) if result.result.type == "succeeded" else None

    # One label per bundle; the snapshot's lone articles (no bundle) as one label
    # of one-article events, so every news article has a label. That they're
    # alone rests on the bundling being loose enough to catch any relation.
    labels, bad, taken = [], 0, {name: set() for name in snaps}
    for key, (name, arts) in jobs.items():
        parsed = parse(answers.get(key) or {}, len(arts))
        urls = [a["url"] for a in arts]
        taken[name] |= set(urls)
        if parsed is None:
            bad += 1
            continue
        groups, places = parsed
        labels.append(label(name, urls, [[urls[i] for i in g] for g in groups], places, model))
    for name in snaps:
        lone = [u for u in articles_of[name] if u not in taken[name]]
        if lone and any(lb["snapshot"] == name for lb in labels):
            labels.append(label(name, lone, [[u] for u in lone], [None] * len(lone), model))
    used = {lb["snapshot"] for lb in labels}
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        json.dump({"made": datetime.now(UTC).isoformat(), "model": model, "rubric": RUBRIC,
                   "snapshots": {n: s for n, s in snaps.items() if n in used}, "labels": labels}, f, ensure_ascii=False)
    events = sum(len(lb["a"]["groups"]) for lb in labels)
    print(f"wrote {args.out}: {len(labels)} labels ({events} events), {bad} answers that didn't add up")
    if state_path.exists():
        state_path.unlink()

if __name__ == "__main__":
    main()
