"""The ground truth for grouping: which news articles report one event.

Claude labels it (tools/label_week.py) under one rubric: an event is one thing
that happened, as a front page would run it as one headline. It's objective
enough that Claude's labels are the truth; nobody labels this by hand.

Stored as one file per ISO week in DIRECTORY (instance/groundtruth/, or
GROUNDTRUTH_DIR), each holding:

  week, made, model, rubric   which week, when, by which model, under what rules
  windows   {name: {"taken", "items", "itn"}}: two 48-hour windows of
            articles (one in the week, one over the weekend), every article
  news      {window name: {url: true | false}}: stage 1's truth, news or not
  events    {window name: [{"urls": [...], "place": iso | "none" | None}]}:
            every article of the window in exactly one event: stage 2's
            truth, over the articles that are news

The set is kept small and curated rather than big: two windows a week, each
reviewed (review) before it's trusted, and evaluation over the newest weeks.
Old weeks can simply be deleted.
"""
import gzip
import json
import os
import pathlib
from dataclasses import dataclass
from datetime import datetime

KEEP_WEEKS = 12        # evaluation uses at most the newest this many weeks


def directory(path: str | None = None) -> pathlib.Path:
    root = pathlib.Path(__file__).resolve().parent.parent
    return pathlib.Path(path or os.environ.get("GROUNDTRUTH_DIR") or root / "instance" / "groundtruth")


@dataclass
class Window:
    """One labelled 48-hour window."""
    week: str
    name: str
    taken: datetime
    articles: list[dict]               # every article, as the app fetched it
    news: dict[str, bool]              # stage 1's truth: url -> news or not
    itn: dict
    truth: dict[str, str]              # stage 2's truth: news article url -> event id
    places: dict[str, str | None]      # event id -> where it happens

    @property
    def items(self) -> list[dict]:
        """The news articles: what stage 2 groups."""
        return [a for a in self.articles if self.news.get(a["url"]) and a["url"] in self.truth]

    @property
    def outlet(self) -> dict[str, str]:
        return {a["url"]: a["outlet"] for a in self.items}


def weeks(path: str | None = None) -> list[str]:
    """The weeks stored, oldest first ("2026-W43")."""
    return sorted(p.name.removesuffix(".json.gz") for p in directory(path).glob("*-W*.json.gz"))


def load(week: str, path: str | None = None) -> list[Window]:
    from labels import read_snapshot
    from news import video_page
    with gzip.open(directory(path) / f"{week}.json.gz", "rt", encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for name, raw in sorted(data["windows"].items()):
        articles, itn, taken = read_snapshot(raw)
        news = data.get("news", {}).get(name) or {a["url"]: True for a in articles}   # weeks before stage 1's truth
        news |= {a["url"]: False for a in articles if a["url"] in news and video_page(a)}   # the rule, not the labeller
        events = data["events"].get(name, [])
        truth = {u: f"{name}/{k}" for k, e in enumerate(events) for u in e["urls"] if news.get(u)}
        out.append(Window(week, name, taken, articles, news, itn, truth,
                          {f"{name}/{k}": e.get("place") for k, e in enumerate(events)}))
    return out


def windows(path: str | None = None, newest: int = KEEP_WEEKS) -> list[Window]:
    """Every window of the newest `newest` weeks, oldest first."""
    return [w for week in weeks(path)[-newest:] for w in load(week, path)]


def save(week: str, data: dict, path: str | None = None) -> pathlib.Path:
    folder = directory(path)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{week}.json.gz"
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump({"week": week, **data}, f, ensure_ascii=False)
    return out


def review(window: Window) -> dict:
    """What a window's partition looks like, to check before trusting it: how
    many events, how many carry to headline size, how much the biggest
    dominate, how many are single articles, and the biggest by name."""
    sizes: dict[str, int] = {}
    outlets: dict[str, set] = {}
    for a in window.items:
        event = window.truth[a["url"]]
        sizes[event] = sizes.get(event, 0) + 1
        outlets.setdefault(event, set()).add(a["outlet"])
    order = sorted(sizes, key=sizes.get, reverse=True)
    n = sum(sizes.values()) or 1
    title = {window.truth[a["url"]]: a["title"] for a in reversed(window.items)}
    return {"articles": n, "events": len(sizes),
            "all_articles": len(window.articles),
            "not_news_share": sum(not v for v in window.news.values()) / (len(window.news) or 1),
            "headline_size": sum(len(o) >= 3 for o in outlets.values()),
            "top5_share": sum(sizes[e] for e in order[:5]) / n,
            "single_share": sum(s == 1 for s in sizes.values()) / (len(sizes) or 1),
            "no_place": sum(window.places.get(e) is None for e in sizes) / (len(sizes) or 1),
            "biggest": [(sizes[e], len(outlets[e]), title[e]) for e in order[:5]]}


def describe(window: Window) -> str:
    r = review(window)
    lines = [f"{window.week} {window.name}: {r['all_articles']} articles, {r['not_news_share']:.0%} not news; "
             f"{r['articles']} news articles in {r['events']} events, "
             f"{r['headline_size']} headline-size; the biggest five hold {r['top5_share']:.0%}, "
             f"{r['single_share']:.0%} are single articles"]
    lines += [f"    {size:3} articles, {n:2} outlets  {title[:80]}" for size, n, title in r["biggest"]]
    if r["top5_share"] > 0.15:
        lines.append("    ! the biggest events dominate: check they're single events, not situations")
    return "\n".join(lines)
