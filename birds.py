"""Birds: the day's bird, one of the world's best known, with Wikipedia's account of it, its
name in Hong Kong's Chinese and a photograph of it (a section of the issue: dispatch.py).

As the issue is made up, Wikidata lists the bird species with a photograph and an article in
English and in Chinese, written about in FAMOUS languages or more (a thousand or so: the
birds the world knows), and gives what it knows of one that hasn't run. Wikipedia gives its
account (CC BY-SA) and its Chinese name in Hong Kong's forms, Wikimedia Commons its
photograph, which is shown only under an open licence, with who took it.
"""
import re
from datetime import date, datetime
from urllib.parse import unquote

import requests

from models import Shown
from sources import clean, get

NAME = "bird"
SOURCES = {"bird:wiki": "Wikipedia"}
FAMOUS = 40             # languages a species is written about in
TRIES = 3               # birds read, at most, for one the issue can show
WIDTH = 1200            # pixels: the photograph as the page asks for it
WIKIDATA = "https://query.wikidata.org/sparql"
SLOW = 60               # seconds Wikidata may take over a query it hasn't been asked lately (its own limit)
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
ZH = "https://zh.wikipedia.org/w/api.php"
COMMONS = "https://commons.wikimedia.org/w/api.php"
# A bird is a species with an Avibase ID (only birds have one), a Latin name, a photograph, and
# articles in English and Chinese. The list is their ids alone; what's known of one, asked after.
_KNOWN = f"""?bird wdt:P2026 []; wdt:P105 wd:Q7432; wdt:P225 ?latin; wdt:P18 ?images; wikibase:sitelinks ?n.
  FILTER(?n >= {FAMOUS})
  ?en schema:about ?bird; schema:isPartOf <https://en.wikipedia.org/>.
  ?zh schema:about ?bird; schema:isPartOf <https://zh.wikipedia.org/>."""
SPECIES = f"SELECT DISTINCT ?bird WHERE {{ {_KNOWN} }}"
ABOUT = f"""SELECT ?latin (SAMPLE(?statuses) AS ?status) (SAMPLE(?images) AS ?image) ?en ?zh WHERE {{
  VALUES ?bird {{ wd:%s }} {_KNOWN}
  OPTIONAL {{ ?bird wdt:P141 ?s. ?s rdfs:label ?statuses. FILTER(LANG(?statuses) = "en") }}
}} GROUP BY ?latin ?en ?zh"""
OPEN = re.compile(r"^(cc0|pd|cc-by(-sa)?-[\d.]+)", re.I)     # Commons' licence codes that let it be shown


def _ask(query: str) -> list[dict]:
    """Wikidata's answers to a query, each {name: value}."""
    rows = get(WIKIDATA, timeout=SLOW, query=query, format="json").json()["results"]["bindings"]
    return [{k: v["value"] for k, v in r.items()} for r in rows]


def _last(url: str) -> str:
    """The last part of an address, as written: a Wikidata id, a page's title, a file's name."""
    return url.rsplit("/", 1)[1]


def species() -> list[str]:
    """The birds the issue can take, by Wikidata id."""
    return [_last(r["bird"]) for r in _ask(SPECIES)]


def photograph(file: str) -> dict | None:
    """A photograph on Commons, at WIDTH, with its licence and who took it; None unless it's open."""
    pages = get(COMMONS, action="query", titles=f"File:{file}", prop="imageinfo", iiprop="url|size|extmetadata",
                iiurlwidth=WIDTH, format="json", formatversion=2).json()["query"]["pages"]
    info = pages[0]["imageinfo"][0]
    meta = {k: v.get("value", "") for k, v in info["extmetadata"].items()}
    if not OPEN.match(meta.get("License", "")):
        return None
    return {"figure": info["thumburl"].split("?")[0], "ratio": round(info["width"] / info["height"], 3),
            "credit": clean(re.sub(r"<[^>]+>", " ", meta.get("Artist", ""))),
            "licence": meta.get("LicenseShortName", ""), "photo": info["descriptionurl"]}


def chinese(title: str) -> str:
    """A Chinese Wikipedia title in Hong Kong's forms (it's written in either)."""
    pages = get(ZH, action="query", titles=title, prop="info", inprop="varianttitles", redirects=1,
                format="json", formatversion=2).json()["query"]["pages"]
    return pages[0].get("varianttitles", {}).get("zh-hk") or title


def bird(key: str) -> dict | None:
    """What the issue keeps of a bird, or None when its photograph can't be shown."""
    [known] = _ask(ABOUT % key)
    picture = photograph(unquote(_last(known["image"])))
    if picture is None:
        return None
    summary = get(SUMMARY + _last(known["en"])).json()
    return {"id": key, "source": "Wikipedia", "title": summary["title"], "latin": known["latin"],
            "zh": chinese(unquote(_last(known["zh"]))), "status": known.get("status", "").capitalize(),
            "text": clean(summary["extract"]), "url": summary["content_urls"]["desktop"]["page"], **picture}


def take(edition: str, day: date, now: datetime, report: dict) -> dict | None:
    """A bird that hasn't run, or None when Wikidata or Wikipedia doesn't answer."""
    try:
        listed = species()
        found = Shown.first(NAME, listed, edition, bird, TRIES)
        report["bird:wiki"] = (len(listed), None) if found else (0, "no open photograph")
        return found
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError) as e:
        report["bird:wiki"] = (0, type(e).__name__)
        return None
