"""Poems: the day's poem to learn by heart, one of 唐詩三百首 or 宋詞三百首, or some days a piece of
古文觀止 with the dictionary's notes on its words, in Hong Kong's characters (a section of the
issue: dispatch.py).

The one section whose texts are kept in the app (data/), not read as the issue is made up:
they were put right from several transcriptions, the notes matched to their words and the
typeface cut to their characters (tools/add_poems.py, tools/add_passages.py), none of which a
source does for the morning.
"""
import json
import pathlib
from datetime import date, datetime

from models import Shown

NAME = "poem"
SOURCES: dict[str, str] = {}
DATA = pathlib.Path(__file__).parent / "data"
# 唐詩三百首 and 宋詞三百首 and, taking a poem's turn, the pieces of 古文觀止 with their notes.
POEMS = [p for name in ("poems.json", "passages.json") for p in json.loads((DATA / name).read_text(encoding="utf-8"))]
BY_KEY = {f"{p['book']}:{p['author']}:{p['title']}:{p['lines'][0]}": p for p in POEMS}


def take(edition: str, day: date, now: datetime, report: dict) -> dict:
    """The poem whose turn it is: each comes round once before any comes again."""
    return Shown.first(NAME, list(BY_KEY), edition, BY_KEY.get)
