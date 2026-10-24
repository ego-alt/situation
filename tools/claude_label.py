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
MAX_BUNDLE = 80           # bigger bundles are cut again, a little stricter (40 split running stories)

RUBRIC = """You label news articles for a personal news filter that shows a handful of stories a day. Sort the \
articles into stories: what the reader wants under one headline. The reader thinks in situations, not in \
individual developments.

- A story is one situation in the news: an event, or a running situation such as a war, an election campaign, an \
outbreak, a protest wave in one country, a trial, a diplomatic push. Everything reporting on it is one story: its \
developments, side events, reactions and consequences, and analysis or explainers about it. The Russia-Ukraine war \
is one story: peace talks, a prisoner freed during them, a battlefield offensive, the Pope's appeal for a ceasefire, \
sanctions or gas deals about the war. Brazil's presidential run-off is one story: debates, polls, court rulings on \
the campaigns, rows over foreign meddling. The Houthi-Saudi war is one story: strikes in Yemen and on Saudi \
Arabia, and conferences postponed or votes delayed because of them.
- Except decisive moments, which are their own story: a war or invasion beginning or ending, a ceasefire or peace \
deal signed; an election or referendum result declared, a verdict in a major trial; a head of state or government \
killed, ousted, resigning or dying. Everything else, however dramatic, stays with its situation.
- Different situations are different stories even when they share people or places: a fire at an Alberta army \
base and Alberta's referendum; Israel's election (a US endorsement of a candidate, a US minister visiting days \
before the vote) and Russia's diplomacy with Iran. The same kind of event in different countries is different \
stories (France's riots, Portugal's cost-of-living protests), but one outbreak, war or disaster that spreads \
across borders is one story. Separate incidents in a wider rivalry are separate stories too (China shadowing a US \
ship near a disputed shoal; China watching the Philippines resupply its warship; China's coast guard near \
Taiwan's Pratas): only a war or a single crisis joins them.
- Analysis, explainers, investigations, look-backs and aftermath features about a situation go with it (children \
recovering after the Tai Po fire, with the fire; an investigation into a massacre warned of during Haiti's gang \
crisis, with the crisis). Features about a wider issue that the situation is only part of stand on their own \
(daily life under settler violence in the West Bank, apart from settlers attacking European diplomats).
- Pages that aren't articles (scores and stats pages, topic pages, TV news bulletins) each stand alone.
- Every article goes in exactly one story. A story can be a single article.

For each story, say where it is centred: the ISO 3166-1 alpha-2 code of the country that is the situation's home \
(a war's main theatre, an election's country, where a summit or trial is held, where a leader's visit abroad takes \
place: Macron securing diesel from Qatar on a Gulf tour is in Qatar), not of those commenting or acting from afar. Hong Kong is HK, Taiwan TW, the Palestinian territories PS, Kosovo XK. Use "none" when it isn't centred \
in any one country (world markets, a global study). Under "also", list other countries where significant action in \
it is happening now (Saudi Arabia, where strikes land, for the Houthi war centred in Yemen); otherwise leave it empty.

Answer only by calling record_stories, once, with every article in it."""

# The second pass: stories split across bundles, mostly running situations
# whose items share few words (the war's talks, its fighting, an appeal).
SITUATIONS = """Below are the stories found in a 48-hour window of news, each with a number, where it's centred and up \
to three of its headlines. They were found in separate batches, so some are parts of the same story under the rules \
above: most often one running situation (a war, an election campaign) split into several. List each set of story \
numbers that is one story. Keep decisive moments apart from their situation: a referendum or election result \
is its own story, never joined to the campaign before it. Leave out stories that stand alone. Answer only by calling record_situations, once."""

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
                "also": {"type": "array", "items": {"type": "string"},
                         "description": "Other countries where significant action is happening now."},
            },
            "required": ["articles", "place"],
        }}},
        "required": ["stories"],
    },
}


SITUATIONS_TOOL = {
    "name": "record_situations",
    "description": "Record which stories are one story.",
    "input_schema": {
        "type": "object",
        "properties": {"groups": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}},
                                  "description": "Each a list of two or more story numbers that are one story."}},
        "required": ["groups"],
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
            "tool_choice": {"type": "auto"},          # newer models don't take a forced tool; the rubric asks for it
            "messages": [{"role": "user", "content": prompt(articles)}]}


def _country(code) -> str | None:
    code = str(code or "").strip()
    return "none" if code.lower() == "none" else code.upper() if code.upper() in _table()[0] else None


def parse(answer: dict, count: int) -> tuple[list[list[int]], list[str | None], list[list[str]]] | None:
    """(groups of article indexes, where each is centred, other countries for each),
    or None if the answer doesn't add up."""
    groups, places, also, seen = [], [], [], set()
    for story in answer.get("stories", []):
        members = [n - 1 for n in story.get("articles", []) if isinstance(n, int) and 1 <= n <= count]
        if not members or seen & set(members):
            return None
        seen |= set(members)
        groups.append(members)
        places.append(_country(story.get("place")))
        also.append([c for c in map(_country, story.get("also") or []) if c and c != "none"])
    if len(seen) != count:
        return None                      # an article left out
    return groups, places, also


def label(name: str, urls: list[str], groups: list[list[str]], places: list, also: list, model: str) -> dict:
    return {"kind": "grouping", "snapshot": name, "source": f"claude:{model}",
            "a": {"bundle": urls, "groups": groups}, "b": {"places": places, "also": also}}


def situations(client, model: str, stories: list[dict]) -> list[list[int]]:
    """The second pass over one snapshot: sets of story indexes that are one story."""
    lines = []
    for n, st in enumerate(stories, 1):
        heads = " | ".join(t[:110] for t in st["titles"][:3])
        lines.append(f"[{n}] {st['place'] or '?'} · {heads}")
    # A big window's answer is long (8,000 tokens ran out at 1,100 stories): stream it.
    with client.messages.stream(
        model=model, max_tokens=32000, system=RUBRIC.replace("Answer only by calling record_stories, once, with every "
                                                             "article in it.", "").strip(),
        tools=[SITUATIONS_TOOL], tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": SITUATIONS + "\n\n" + "\n".join(lines)}]) as stream:
        message = stream.get_final_message()
    answer = next((b.input for b in message.content if b.type == "tool_use"), None)
    if answer is None:
        raise RuntimeError(f"the second pass gave no answer (stopped: {message.stop_reason}); "
                           f"{message.usage.input_tokens} tokens in, {message.usage.output_tokens} out")
    out, seen = [], set()
    for g in answer.get("groups", []):
        members = sorted({n - 1 for n in g if isinstance(n, int) and 1 <= n <= len(stories)} - seen)
        if len(members) >= 2:
            seen |= set(members)
            out.append(members)
    return out


def merge(name: str, items: list[dict], found: list[dict], links: list[list[int]], model: str) -> list[dict]:
    """One snapshot's labels once the second pass has joined stories across
    bundles: bundles a situation spans become one label (so the pairs across
    them are judged too), the rest stay as they were.

    found: the stories, each {"urls", "place", "also", "bundle": its bundle's index or None (a lone article)}."""
    owner = list(range(len(found)))              # story → the story it was joined into
    for g in links:
        for k in g:
            owner[k] = g[0]
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            x = parent[x]
        return x
    for g in links:                              # bundles (or lone articles) a situation spans become one
        keys = [found[k]["bundle"] if found[k]["bundle"] is not None else ("lone", k) for k in g]
        for key in keys[1:]:
            parent[find(key)] = find(keys[0])
    regions: dict = {}
    for k, st in enumerate(found):
        key = st["bundle"] if st["bundle"] is not None else ("lone", k)
        if st["bundle"] is None and find(key) == key:
            continue                             # a lone article no situation took in: nothing judged
        regions.setdefault(find(key), []).append(k)
    labels = []
    for members in regions.values():
        stories: dict[int, list[int]] = {}
        for k in members:
            stories.setdefault(owner[k], []).append(k)
        groups, places, also, urls = [], [], [], []
        for parts in stories.values():
            parts.sort(key=lambda k: -len(found[k]["urls"]))
            groups.append([u for k in parts for u in found[k]["urls"]])
            places.append(next((found[k]["place"] for k in parts if found[k]["place"]), None))
            also.append(sorted({c for k in parts for c in found[k]["also"]} - {places[-1]}))
            urls += groups[-1]
        labels.append(label(name, urls, groups, places, also, model))
    return labels


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
    p.add_argument("--no-situations", action="store_true", help="skip the second pass (joining stories across bundles)")
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

    # Each snapshot's stories: those Claude found in its bundles, then the lone articles.
    found: dict[str, list[dict]] = {name: [] for name in snaps}
    bad = 0
    for key, (name, arts) in jobs.items():
        parsed = parse(answers.get(key) or {}, len(arts))
        if parsed is None:
            bad += 1
            continue
        for g, place, also in zip(*parsed, strict=True):
            found[name].append({"urls": [arts[i]["url"] for i in g], "titles": [arts[i]["title"] for i in g],
                                "place": place, "also": also, "bundle": key})
    labels = []
    for name, stories in found.items():
        if not stories:
            continue
        taken = {u for st in stories for u in st["urls"]}
        stories += [{"urls": [a["url"]], "titles": [a["title"]], "place": None, "also": [], "bundle": None}
                    for a in articles_of[name].values() if a["url"] not in taken]
        links = [] if args.no_situations else situations(client, model, stories)
        print(f"{name}: {len(stories)} stories, {len(links)} sets joined by the second pass")
        labels += merge(name, list(articles_of[name].values()), stories, links, model)
    used = {lb["snapshot"] for lb in labels}
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        json.dump({"made": datetime.now(UTC).isoformat(), "model": model, "rubric": RUBRIC,
                   "snapshots": {n: s for n, s in snaps.items() if n in used}, "labels": labels}, f, ensure_ascii=False)
    stories = sum(len(lb["a"]["groups"]) for lb in labels)
    print(f"wrote {args.out}: {len(labels)} labels ({stories} stories), {bad} answers that didn't add up")
    if state_path.exists():
        state_path.unlink()

if __name__ == "__main__":
    main()
