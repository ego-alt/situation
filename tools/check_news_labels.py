"""A second labeller for stage 1: another model labels every article the first
did (the ground truth windows and the training articles), and where the two
disagree is printed. Run on the Mac; needs ANTHROPIC_API_KEY.

    uv run --with anthropic python -I tools/check_news_labels.py [--model claude-opus-5-5]

The labels go to news-check.json.gz in the ground truth folder and accumulate
run by run (a rubric change starts over). tools/evaluate_news.py then scores
on the articles both labellers agree on as well as on all, and trains only on
agreed ones. Agreement is the ceiling for any method scored against the first
labels; where they disagree shows what the rubric (tools/label_week.py
NEWS_RUBRIC) leaves unclear. Every article is checked, not just where a model
errs, so the check can't favour the model. About $5 for 5,000 articles with Opus.
"""
import argparse
import concurrent.futures as cf
import gzip
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import label_news  # noqa: E402
from label_week import NEWS_CHUNK, NEWS_RUBRIC, answer_of, news_request, parse_news  # noqa: E402

import news  # noqa: E402
from evaluation import groundtruth  # noqa: E402


def path_of(folder: str | None) -> pathlib.Path:
    return groundtruth.directory(folder) / "news-check.json.gz"


def load(folder: str | None = None) -> dict[str, bool]:
    """{url: the second labeller's news-or-not}, empty without a check (or after a rubric change)."""
    p = path_of(folder)
    if not p.exists():
        return {}
    with gzip.open(p, "rt", encoding="utf-8") as f:
        store = json.load(f)
    return store["labels"] if store["rubric"] == NEWS_RUBRIC else {}


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--model", default="claude-opus-5-5")
    p.add_argument("--dir", default=None)
    args = p.parse_args()
    labels = load(args.dir)
    first = {a["url"]: (a, w.news[a["url"]])
             for w in groundtruth.windows(args.dir) for a in w.articles if a["url"] in w.news}
    first |= {u: (a, a["news"]) for u, a in label_news.load(args.dir)["articles"].items()}
    todo = [a for u, (a, _) in first.items() if u not in labels]
    chunks = [todo[k:k + NEWS_CHUNK] for k in range(0, len(todo), NEWS_CHUNK)]
    print(f"{len(first)} labelled articles, {len(labels)} checked already, "
          f"{len(todo)} to check in {len(chunks)} requests")

    if chunks:
        import anthropic
        client = anthropic.Anthropic()

        def ask(chunk):
            answer = answer_of(client.messages.create(**news_request(chunk, args.model)))
            return chunk, parse_news(answer or {}, len(chunk))

        failed = 0
        with cf.ThreadPoolExecutor(6) as ex:
            for n, (chunk, theirs) in enumerate(ex.map(ask, chunks), 1):
                if theirs is None:
                    failed += 1
                    continue
                # the video rule, as for the first labels
                labels |= {a["url"]: v and not news.video_page(a) for a, v in zip(chunk, theirs, strict=True)}
                print(f"\r{n}/{len(chunks)}", end="", flush=True)
        with gzip.open(path_of(args.dir), "wt", encoding="utf-8") as f:
            json.dump({"rubric": NEWS_RUBRIC, "model": args.model, "labels": labels}, f)
        print(f"\n{failed} requests didn't add up (run again)")

    both = [(a, ours, labels[u]) for u, (a, ours) in first.items() if u in labels]
    agree = sum(ours == theirs for _, ours, theirs in both)
    print(f"{args.model} agrees on {agree}/{len(both)} ({agree / max(len(both), 1):.1%})")
    for label, name in ((True, "labelled news, the check says not"), (False, "labelled not news, the check says news")):
        cases = [a for a, ours, theirs in both if ours is label and theirs is not label]
        print(f"\n{name} ({len(cases)}):")
        for a in cases[:20]:
            print(f"  {a['source']:10} {a['title'][:95]}")


if __name__ == "__main__":
    main()
