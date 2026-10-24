"""Claude labels whole snapshots: which articles are one story, and where each happens.

Which story is which, and where it happens, have answers a careful reader
would agree on, so Claude can give them in bulk and you can keep your own
labelling for what only you can say (what matters). The labels train the
models in learn.py; yours test them, and test Claude (`flask evaluate` prints
how often Claude agrees with you).

    scp pi:Projects/situation/instance/situation.db /tmp/situation.db
    uv run --with anthropic python -I tools/claude_label.py /tmp/situation.db labels.json.gz --dry-run
    uv run --with anthropic python -I tools/claude_label.py /tmp/situation.db labels.json.gz --direct --limit 10
    uv run --with anthropic python -I tools/claude_label.py /tmp/situation.db labels.json.gz
    scp labels.json.gz pi:Projects/situation/instance/
    ssh pi docker exec situation flask import-labels instance/labels.json.gz

It reads a copy of the database (never the live one), cuts a snapshot every
48 hours from the articles it holds (the window the app groups), and splits
each snapshot into bundles: articles loosely alike, so every story falls
inside one bundle and Claude sees each bundle whole. Singletons are left out.
The run goes through the Message Batches API (half the price; usually done
within the hour); --direct asks one bundle at a time instead, for a quick
look. Needs ANTHROPIC_API_KEY. If the wait is interrupted, run it again with
--resume to collect the batch.
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

MODEL = "claude-sonnet-5-5"
WINDOW = timedelta(hours=48)
BUNDLE = 0.15             # word likeness that puts two articles in one bundle
MEANING = 0.55            # ...or meaning likeness, with the embedding model
MAX_BUNDLE = 40           # bigger bundles are cut again, a little stricter

RUBRIC = """You label news articles for a personal news filter. Sort the articles you're given into stories: \
groups a reader would want under one headline.

- A story is one event or one running situation, with its developments and the direct reactions to it: \
riots, the president's address about them, and the arrests after them are one story.
- The same kind of event in different places is different stories (riots in France, riots in Italy). Two events \
that only share a place or a person are different stories (a fire at an Alberta army base; Alberta's referendum).
- A round-up or opinion piece goes with the story it is mostly about; if it isn't mostly about one, it stands alone.
- Every article goes in exactly one story. A story can be a single article.

For each story, say where it is happening: the ISO 3166-1 alpha-2 code of the country where the events take \
place, not of those commenting or acting from afar ("Washington warns Iran over strikes in Iraq" happens in Iraq). \
Hong Kong is HK, Taiwan TW, the Palestinian territories PS, Kosovo XK. A summit or trial happens where it's held. \
Use "none" when it isn't happening in any one country (world markets, a global study, a worldwide outage)."""

TOOL = {
    "name": "record_stories",
    "description": "Record how the articles split into stories, and where each story happens.",
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
        items = [{"source": r["source"], "title": r["title"], "url": r["url"], "summary": r["summary"],
                  "position": r["position"], "published": t.isoformat()}
                 for t, r in articles if taken - WINDOW < t <= taken]
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
            "tool_choice": {"type": "tool", "name": "record_stories"},
            "messages": [{"role": "user", "content": prompt(articles)}]}


def parse(answer: dict, count: int) -> tuple[list[list[int]], list[str | None]] | None:
    """(groups of article indexes, a place for each), or None if the answer doesn't add up."""
    countries = _table()[0]
    groups, places, seen = [], [], set()
    for story in answer.get("stories", []):
        members = [n - 1 for n in story.get("articles", []) if isinstance(n, int) and 1 <= n <= count]
        if not members or seen & set(members):
            return None
        seen |= set(members)
        groups.append(members)
        place = str(story.get("place", "")).strip()
        places.append("none" if place.lower() == "none" else place.upper() if place.upper() in countries else None)
    if len(seen) != count:
        return None                      # an article left out
    return groups, places


def label(name: str, articles: list[dict], parsed, model: str) -> dict:
    groups, places = parsed
    return {"kind": "grouping", "snapshot": name, "source": f"claude:{model}",
            "a": {"bundle": [a["url"] for a in articles], "groups": [[articles[i]["url"] for i in g] for g in groups]},
            "b": {"places": places}}


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
        work = work[:args.limit] if args.limit else work
    articles_of = {}
    for name, snap in snaps.items():
        items, _, _ = read_snapshot(snap)
        articles_of[name] = {a["url"]: a for a in items}
    jobs = {w["id"]: (w["snapshot"], [articles_of[w["snapshot"]][u] for u in w["urls"]]) for w in work}

    tokens_in = sum(len(RUBRIC) + len(json.dumps(TOOL)) + len(prompt(arts)) for _, arts in jobs.values()) / 3.5
    print(f"{len(snaps)} snapshots, {len(jobs)} bundles, {sum(len(a) for _, a in jobs.values())} articles; "
          f"about {tokens_in / 1e6:.2f}M input tokens and {len(jobs) * 150 / 1e6:.2f}M output, with {model}")
    if args.dry_run:
        return

    import anthropic
    client = anthropic.Anthropic()
    answers: dict[str, dict | None] = {}
    if args.direct:
        for n, (key, (_, arts)) in enumerate(jobs.items(), 1):
            answers[key] = answer_of(client.messages.create(**request(arts, model)))
            print(f"\r{n}/{len(jobs)}", end="", flush=True)
        print()
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

    labels, bad = [], 0
    for key, (name, arts) in jobs.items():
        parsed = parse(answers.get(key) or {}, len(arts))
        if parsed is None:
            bad += 1
            continue
        labels.append(label(name, arts, parsed, model))
    used = {lb["snapshot"] for lb in labels}
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        json.dump({"made": datetime.now(UTC).isoformat(), "model": model, "rubric": RUBRIC,
                   "snapshots": {n: s for n, s in snaps.items() if n in used}, "labels": labels}, f, ensure_ascii=False)
    stories = sum(len(lb["a"]["groups"]) for lb in labels)
    print(f"wrote {args.out}: {len(labels)} bundles labelled ({stories} stories), {bad} answers that didn't add up")
    if state_path.exists():
        state_path.unlink()


if __name__ == "__main__":
    main()
