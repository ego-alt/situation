"""Stage 1 of the pipeline: is an article news? (docs/phases.md)

News reports something that happened or is happening, live blogs included.
Not news: opinion, analysis (even about today), explainers, features,
profiles, look-backs, round-ups, reviews, galleries, video-only items, and
pages that aren't articles. Only news is grouped and shown; the database keeps
everything.

The evidence, all from the feed (an article's page added nothing measurable,
and many outlets refuse the fetch):

  headline   phrases that name a format ("Opinion:", "What we know", a
             question)
  url        the path outlets file a piece under (/opinion/, /commentisfree/,
             /video/, /features/)

  formats    how the piece is built (formats): a round-up's name, an
             explainer's question, a columnist's byline, a feature's
             opening; signals for the learned model, not rules. A video page
             (video_page) is never news, and the labels follow that rule.

news_only is what the pipeline uses: the learned model below when it's saved,
the rules (is_news) otherwise. tools/evaluate_news.py measures both against
the ground truth (evaluation/groundtruth.py) and trains the model.
"""
import html
import re
from urllib.parse import urlparse

# Headlines that name a format, not the news.
_HEADLINE = re.compile(
    r"\b(opinion|analysis|comment|commentary|editorial|editorial cartoon|letters?(?: to the editor)?|column|"
    r"explainer|explained|what to know|what we know|what you need to know|q&a|fact ?check|"
    r"podcast|newsletter|quiz|crossword|in pictures|photos? of the (?:day|week)|gallery|"
    r"the week in|year in review|a look back|timeline|scores?, news (?:&|and) stats|evening news|morning brief(?:ing)?|"
    r"first thing|today in history|horoscope)\b|\?\s*$", re.I)

# Path segments outlets file non-news under. Live pages (/live/) are news.
_PATH = re.compile(
    r"/(opinions?|commentisfree|comment|commentary|editorials?|columns?|columnists?|letters|analysis|"
    r"explainers?|features?|long-?reads?|magazines?|lifestyle|travel|food|culture|style|books|"
    r"videos?|tv-shows|programs?|programmes|podcasts?|in-pictures|pictures|gallery|galleries|photos?|"
    r"interactive|newsletters?|quizzes|crosswords|puzzles|obituaries)(/|$)", re.I)


def headline_says_not_news(article: dict) -> bool:
    title = article["title"]
    return len(title.split()) < 3 or bool(_HEADLINE.search(title))


def url_says_not_news(article: dict) -> bool:
    return bool(_PATH.search(urlparse(article.get("url") or "").path))


def is_news(article: dict) -> bool:
    """A report of something happening, as far as the cheap evidence tells."""
    return not (headline_says_not_news(article) or url_says_not_news(article))


# A video page is not news, whatever its caption says: the rule, and the labels follow it.
_VIDEO_PATH = re.compile(r"/(videos?|tv-shows|programs?|programmes|audio)/|/replay-", re.I)
_VIDEO_HEADLINE = re.compile(r"^\W*(video|watch|replay|live video)\b\s*[:|-]|^footage shows\b", re.I)


def video_page(article: dict) -> bool:
    return bool(_VIDEO_PATH.search(urlparse(article.get("url") or "").path) or _VIDEO_HEADLINE.search(article["title"]))


# Formats ---------------------------------------------------------------------------
# How a piece is built, not what it's about. Opinion, analysis and round-ups carry
# news inside them; what gives them away is the format: a round-up's name, an
# explainer's question, a columnist's byline, a feature's anecdote.

_FORMATS = {
    "roundup": (r"\b(morning bid|first thing|europe today|quick take|highlights|press review|the latest|briefing|"
                r"newsletter|week ahead|busy week|catch up|top stories)\b|"
                r"\band other\b.*\b(news|developments|stories)\b|^\d+ (things|stories)\b",
                r"\b(in (tonight's|today's|this) (programme|edition|episode)|hello from|newsletter pen|"
                r"we have selected|top stories|catch up with)\b|(^|\. )plus,"),
    "explainer": (r"^(how|why|what|inside|takeaways|a look|here's|explainer)\b|\bhow\b.*\bworks\b|\bquestions\b|"
                  r"\banswered\b|\bwhat (to|you) (know|need)|\bthis is how\b|\bpredictions\b|\bhere's\b|explained",
                  None),
    "voice": (r"\b(experts?|analysts?|observers?|economists?) (says?|warns?|believes?)\b|"
              r"^(insight|analysis|opinion|comment|view|perspective|the big take)\b|"
              r"\|\s*[a-z]+ [a-z]+\s*$|: big take\b",
              r",? writes\b|\bwrites the\b|\bour (correspondent|reporter)\b.*\b(has more|reports)\b"),
    "gallery": (r"\bin (photos|pictures)\b|\bphotos? (show|of)\b|\btop photos\b|\bpictures? of the\b", None),
}
_ADVERTORIAL = re.compile(r"/(brand-studio|advertorial|presented|sponsored|partner-content|branded)/", re.I)
_DAYS = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_SHAPES = {
    "headline_the": r"^the\b",                       # "The human cost of...", "The Other Bolsonaro..."
    "headline_colon": r"\w.*: \w",                   # two parts: "Paris, the day after: A city on edge"
    "headline_quote": r"^\W*[‘“'\"]",               # led by a quote
    "headline_question": r"\?\s*$",
}
# A news summary opens with who did what, and when; a feature's with a scene or a person.
_LEDES = {
    "lede_when": rf"\b(on|this|last|late|early) {_DAYS}\b|\b(yesterday|today|tonight|on \w+ \d{{1,2}})\b",
    "lede_said": r"\b(said|says|announced|confirmed|told|according to|reported)\b",
    "lede_scene": r"^(when|before|after|as|it was|it took|on a|in the|for (years|decades|months))\b|"
                  r"\bhad (already|long|never|been)\b",
    "lede_person": r"\b(i|my|we|you|your)\b",
}


def formats(article: dict) -> dict[str, bool]:
    """Yes/no signals of a piece's format, from its headline, summary and url."""
    title = " ".join(html.unescape(article["title"]).lower().split())
    summary = " ".join(html.unescape(article.get("summary") or "").split())
    opening = " ".join(summary.split()[:30]).lower()
    out = {"video": video_page(article), "advertorial": bool(_ADVERTORIAL.search(article.get("url") or ""))}
    for name, (in_title, in_summary) in _FORMATS.items():
        out[name] = bool(re.search(in_title, title) or (in_summary and re.search(in_summary, opening)))
    out |= {name: bool(re.search(pattern, title)) for name, pattern in _SHAPES.items()}
    out |= {
        "no_summary": not summary,
    }
    out |= {name: bool(re.search(pattern, opening)) for name, pattern in _LEDES.items()}
    return out


# The learned model ---------------------------------------------------------------
# A logistic regression over the evidence above, the embedding (embed.py) cut to its
# 64 main directions, and outlet and url-path tokens: 150 numbers an article. Trained
# on the Mac against Claude's labels (tools/evaluate_news.py --save) and saved as
# instance/models/news.json; applying it is a little arithmetic. Without the file or
# the embedding model, the rules (is_news) decide.

_PROMO = re.compile(r"(Get our|Sign up for|Follow our)\b.{0,80}?\bpodcast\s*|Continue reading\.*", re.I)


def clean(text: str | None) -> str:
    """Feed text without HTML entities, newsletter promos and extra spaces."""
    return " ".join(_PROMO.sub(" ", html.unescape(text or "")).split())


def summary(article: dict, words: int = 60) -> str:
    return " ".join(clean(article.get("summary")).split()[:words])


def evidence(article: dict) -> list[bool]:
    """The rules and the format signals, in the order of evidence_names()."""
    return [headline_says_not_news(article), url_says_not_news(article), *formats(article).values()]


def evidence_names() -> list[str]:
    return ["headline", "url", *formats({"title": "", "url": ""})]


def tokens(article: dict) -> list[str]:
    """The outlet and the url's first three path segments (no ids or slugs)."""
    path = [s for s in re.split(r"[/\-_.]", urlparse(article.get("url") or "").path.lower())
            if s and len(s) < 25 and not re.search(r"\d{3}", s)][:3]
    return [f"outlet:{article['source']}"] + [f"path:{s}" for s in path]


def meaning_text(article: dict) -> str:
    return f"{clean(article['title'])}. {summary(article, 40)}"


def matrix(articles: list[dict], model: dict):
    """Each article's numbers for the model (its mean, components and tokens), or
    None without the embedding model."""
    import numpy as np

    import embed
    vectors = embed.encode([meaning_text(a) for a in articles])
    if vectors is None:
        return None
    index = {t: i for i, t in enumerate(model["tokens"])}
    flags = np.zeros((len(articles), len(index)))
    for row, a in enumerate(articles):
        for t in tokens(a):
            if t in index:
                flags[row, index[t]] = 1.0
    meaning = (vectors - np.array(model["mean"])) @ np.array(model["components"]).T
    return np.hstack([np.array([evidence(a) for a in articles], dtype=float), meaning, flags])


def chances(articles: list[dict], model: dict):
    """Each article's chance of not being news, or None when the model can't be
    applied (no embedding model, or saved for other evidence)."""
    import numpy as np
    if model.get("evidence") != evidence_names():
        return None
    x = matrix(articles, model)
    return None if x is None else 1 / (1 + np.exp(-(x @ np.array(model["weights"]) + model["bias"])))


def news_only(items: list[dict]) -> list[dict]:
    """The articles that are news: by the learned model when it's saved, else the rules."""
    import learn
    model = learn.load("news")
    p = chances(items, model) if model and items else None
    if p is None:
        return [a for a in items if is_news(a)]
    return [a for a, chance in zip(items, p, strict=True) if chance < model["threshold"]]
