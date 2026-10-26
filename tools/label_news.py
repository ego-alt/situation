"""Label news-or-not for every article in a database copy that has no label yet,
for training stage 1's model (tools/evaluate_news.py). Run on the Mac; needs
ANTHROPIC_API_KEY.

    uv run --with anthropic python -I tools/label_news.py /tmp/situation.db [--dry-run]

Same rubric and requests as tools/label_week.py's news labels, but these are
for training only: articles in a ground truth week's windows are left out, so
the test set stays unseen. Labels go to news-train.json.gz in the ground truth
folder, and accumulate run by run; if the rubric changes, they start over.
About $0.50 per 3,000 articles with Sonnet.
"""
import argparse
import concurrent.futures as cf
import gzip
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from label_week import MODEL, NEWS_CHUNK, NEWS_RUBRIC, answer_of, news_request, parse_news  # noqa: E402

import news  # noqa: E402
from evaluation import groundtruth  # noqa: E402


def path_of(folder: str | None) -> pathlib.Path:
    return groundtruth.directory(folder) / "news-train.json.gz"


def load(folder: str | None = None) -> dict:
    """{"rubric", "model", "articles": {url: article with "news": bool}}."""
    p = path_of(folder)
    if not p.exists():
        return {"rubric": NEWS_RUBRIC, "model": MODEL, "articles": {}}
    with gzip.open(p, "rt", encoding="utf-8") as f:
        store = json.load(f)
    for a in store["articles"].values():
        a["news"] = a["news"] and not news.video_page(a)   # video pages aren't news: the rule, not the labeller
    return store


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("db", help="a copy of instance/situation.db")
    p.add_argument("--dir", default=None)
    p.add_argument("--model", default=MODEL)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    store = load(args.dir)
    if store["rubric"] != NEWS_RUBRIC:
        print("the rubric changed: starting over")
        store = {"rubric": NEWS_RUBRIC, "model": args.model, "articles": {}}
    held_out = {a["url"] for w in groundtruth.windows(args.dir) for a in w.articles}
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT source, title, url, summary FROM articles")]
    con.close()
    todo = [a for a in rows if a["url"] not in held_out and a["url"] not in store["articles"]]
    chunks = [todo[k:k + NEWS_CHUNK] for k in range(0, len(todo), NEWS_CHUNK)]
    print(f"{len(rows)} articles: {len(held_out & {a['url'] for a in rows})} in test windows, "
          f"{len(store['articles'])} labelled already, {len(todo)} to label in {len(chunks)} requests")
    if args.dry_run or not todo:
        return

    import anthropic
    client = anthropic.Anthropic()

    def ask(chunk):
        return chunk, parse_news(answer_of(client.messages.create(**news_request(chunk, args.model))) or {}, len(chunk))

    failed = 0
    with cf.ThreadPoolExecutor(6) as ex:
        for n, (chunk, labels) in enumerate(ex.map(ask, chunks), 1):
            if labels is None:
                failed += 1
                continue
            store["articles"] |= {a["url"]: a | {"news": is_news} for a, is_news in zip(chunk, labels, strict=True)}
            print(f"\r{n}/{len(chunks)}", end="", flush=True)
    with gzip.open(path_of(args.dir), "wt", encoding="utf-8") as f:
        json.dump(store, f)
    share = sum(not a["news"] for a in store["articles"].values()) / max(len(store["articles"]), 1)
    print(f"\n{len(store['articles'])} labelled ({share:.0%} not news); {failed} requests didn't add up (run again)")


if __name__ == "__main__":
    main()
