"""Prints: the day's print, a Japanese or Chinese one from the Cleveland Museum of Art's open
access collection (CC0), whole, with what the museum says of it (a section of the issue:
dispatch.py).

As the issue is made up, the museum's API lists the prints of both departments with their
texts and pictures; one it wrote enough about, of a shape to show beside its text, is taken
(one text is often given to every sheet of a set: the first sheet stands for it), and its
record read for the rest.
"""
import re
from datetime import date, datetime

import requests

from models import Shown
from sources import clean, get

NAME = "print"
SOURCE = "Cleveland Museum of Art"
SOURCES = {"print:cma": f"the {SOURCE}"}
API = "https://openaccess-api.clevelandart.org/api/artworks/"
DEPARTMENTS = ("Japanese Art", "Chinese Art")
WORTH_READING = 250     # characters of the museum's text
SHAPES = (0.5, 2.0)     # width over height: a pillar print or a handscroll is too thin to show beside its text
TRIES = 3               # prints read, at most, for one the issue can show


def paragraphs(parts) -> str:
    """Pieces of the museum's text as one: a blank line between paragraphs, a <br> a new line. Links
    and spans dissolve first (clean leaves a space where a tag was)."""
    inline = re.compile(r"</?(?:a|span|strong|b|u)\b[^>]*>")
    lines = (re.split(r"<br\s*/?>", inline.sub("", part or "")) for part in parts)
    return "\n\n".join(filter(None, ("\n".join(filter(None, map(clean, part))) for part in lines)))


def _shape(a: dict) -> float | None:
    """A print's width over height, if it has a picture to show."""
    image = (a.get("images") or {}).get("web")
    return image and int(image["width"]) / int(image["height"])


def _worth(a: dict) -> bool:
    ratio = _shape(a)
    return bool(ratio) and len(a.get("description") or "") >= WORTH_READING and SHAPES[0] <= ratio <= SHAPES[1]


def _print(a: dict) -> dict:
    """What the issue keeps of a print's record."""
    about, aside = a.get("description") or "", a.get("did_you_know") or ""
    return {"id": a["accession_number"], "source": SOURCE, "title": clean(a["title"]),
            "maker": clean(next((c["description"] for c in a.get("creators") or []), "")),
            "date": a.get("creation_date") or "", "technique": clean(a.get("technique")),
            "text": paragraphs(re.split(r"(?:<br>\s*){2,}", about) + [aside] * (aside not in about)),
            "url": a["url"], "figure": a["images"]["web"]["url"], "ratio": round(_shape(a), 3)}


def take(edition: str, day: date, now: datetime, report: dict) -> dict | None:
    """A print the museum wrote about that hasn't run, or None when the museum doesn't answer."""
    try:
        sheets: dict[str, str] = {}         # a text → the first sheet given it
        for department in DEPARTMENTS:
            listed = get(API, cc0=1, has_image=1, type="Print", department=department, limit=1000,
                         fields="accession_number,description,images").json()["data"]
            for a in filter(_worth, listed):
                sheets.setdefault(a["description"], a["accession_number"])
        found = Shown.first(NAME, list(sheets.values()), edition, _read, TRIES)
        report["print:cma"] = (len(sheets), None) if found else (0, "nothing to show")
        return found
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        report["print:cma"] = (0, type(e).__name__)
        return None


def _read(key: str) -> dict | None:
    """A print's record, as the issue keeps it, if it's one to show."""
    record = get(API + key).json()["data"]
    return _print(record) if _worth(record) else None
