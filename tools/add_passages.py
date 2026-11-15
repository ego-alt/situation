"""Write data/passages.json: the pieces of 古文觀止 that take the poem's place some days, each with
its notes: the words 教育部《重編國語辭典修訂本》 explains out of that very piece.

The text is Project Gutenberg's (gutenberg.org/ebooks/25225, typed in traditional and punctuated),
read against two other transcriptions: Wikisource's (zh.wikisource.org/wiki/古文觀止/卷1 to 卷12,
as wikitext: ?action=raw, in a folder) and the chinese-poetry collection's (MIT,
github.com/chinese-poetry/chinese-poetry: its 蒙学/guwenguanzhi.json). Where Gutenberg's stands
alone against both (泰伯 for 秦伯) it's a slip, and theirs is taken. A piece is left out if that
can't be done in place (settle), so every character kept is in two transcriptions of the three.

The dictionary (g0v's copy, github.com/g0v/moedict-data: its dict-revised.json.xz) gives each
sense of a word a quotation or two. Where one is from the piece (its clause with the word in it,
or all of it, is there), that sense is what the word means there: nothing is guessed at, and
nothing is written for the page. The dictionary's words are kept as they are (its licence,
CC BY-ND 3.0 TW, allows no changing them); the piece is set in Hong Kong's forms, as the poems
are. A piece is kept if it's short enough for a sitting and has notes enough to be worth marking.
The typeface is then cut again (add_poems.cut), to these characters too:

    uv run --with opencc-python-reimplemented --with 'fonttools[woff]' python -I tools/add_passages.py \
        pg25225.txt wikisource/ guwenguanzhi.json dict-revised.json.xz ChironSungHK-R.ttf ChironSungHK-SB.ttf
"""
import json
import lzma
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))     # -I leaves this folder off the path
from add_poems import ASIDES, DATA, HAN, MARKS, cut, differences, finder, fold, han, hk  # noqa: E402

BOOK = "古文觀止"
LONGEST = 700       # characters: a piece to read at a sitting, as the longest poems are
FEWEST = 5          # notes: fewer, and it isn't worth marking
CLAUSE = 4          # characters: a clause this long in both is the same clause
CLOSE = 2           # characters: a slip is this many at most, for as many
HEADING = re.compile(r"^(卷[一二三四五六七八九十]+|附錄.)‧(.+?)　　(.+?)　?$", re.M)
TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
BREAKS = re.compile(f"[^{HAN}]+")
PLAIN = re.compile(f"[{HAN}，。、；：？！「」『』《》\n]*")


def gutenberg(src):
    """Gutenberg's pieces, (by, text): the text is the title, then its paragraphs, an empty line between them."""
    book = pathlib.Path(src).read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    parts = HEADING.split(book[:book.index("*** END")])
    for volume, title, by, text in zip(parts[1::4], parts[2::4], parts[3::4], parts[4::4], strict=True):
        if volume.startswith("卷"):         # the appendices aren't the book's
            paragraphs = [re.sub(r"\s", "", p) for p in re.split(r"\n\s*\n", text.strip())]
            yield by, (title + "\n" + "\n\n".join(paragraphs)).translate(MARKS)


def wikisource(src):
    """Wikisource's pieces, characters only: without the editors' notes ({{*|…}}) and closing remarks (indented)."""
    for page in sorted(pathlib.Path(src).glob("*.wiki")):
        page = re.sub(r"^ .*$", "", page.read_text(encoding="utf-8"), flags=re.M)
        while TEMPLATE.search(page):
            page = TEMPLATE.sub("", page)
        yield from (han(piece)[0] for piece in re.split(r"^(?===[^=])", page, flags=re.M)[1:])


def collection(src):
    """The chinese-poetry collection's pieces, characters only."""
    for volume in json.loads(pathlib.Path(src).read_text(encoding="utf-8"))["content"]:
        for p in volume["content"]:
            yield han(ASIDES.sub("", p["chapter"] + "".join(p["paragraphs"])))[0]


def settle(text: str, others: list[str]) -> str | None:
    """`text` with its slips put right: where it stands alone against both `others` and they read
    alike, theirs is taken. None if that can't be done in place: the three read three ways, or
    it's more than a slip, or (the others being characters only) there's no telling where the
    marks would go: a clause gone, or a word more where a clause starts or ends."""
    chars, at = han(text)
    # How each of the others reads otherwise: (from, to) in the text's characters → what it has there.
    one, other = ({(i1, i2): b[j1:j2] for tag, i1, i2, j1, j2 in differences(chars, b) if tag != "equal"}
                  for b in others)
    for (i1, i2), said in sorted(one.items(), reverse=True):
        if (i1, i2) not in other:           # that transcription's own reading
            continue
        start = at[i1] if i1 < len(at) else len(text)
        end = at[i2 - 1] + 1 if i2 > i1 else start
        before, after = i1 and at[i1 - 1] == start - 1, i2 < len(at) and at[i2] == end      # no mark between
        whole = end - start == i2 - i1 and (before if i2 == i1 else said or before or after)
        if fold(other[i1, i2]) != fold(said) or max(i2 - i1, len(said)) > CLOSE or not whole:
            return None
        text = text[:start] + said + text[end:]
    return text


def pieces(guwen_src, wiki_src, collection_src):
    """The book's pieces, each as a poem is (data/poems.json), its paragraphs as a poem's stanzas
    are: those that have two other transcriptions to be read against, and nothing left that isn't text."""
    witnesses = [list(read(src)) for read, src in ((wikisource, wiki_src), (collection, collection_src))]
    finders = [finder(w) for w in witnesses]
    for by, text in gutenberg(guwen_src):
        found = [find(han(text)[0]) for find in finders]
        text = None if None in found else settle(text, [w[n] for w, n in zip(witnesses, found, strict=True)])
        if text and PLAIN.fullmatch(text):
            title, *lines = hk(text).split("\n")
            yield {"book": BOOK, "title": title, "author": hk(by), "form": "文", "lines": lines}


def senses(src):
    """The dictionary's senses that quote something: (word, kind, meaning, the quotation)."""
    with lzma.open(src, "rt", encoding="utf-8") as f:
        for entry in json.load(f):
            if BREAKS.search(entry["title"]):       # a character it has no code for
                continue
            for reading in entry["heteronyms"]:
                for d in reading.get("definitions", []):
                    for quote in d.get("quote", []):
                        yield entry["title"], d.get("type", ""), d["def"].strip("\ufeff "), quote.partition("：「")[2]


def annotate(found: list[dict], said) -> None:
    """Give each piece its notes, in reading order: {"line", "start", "end", "senses"}, the
    senses [{"word", "kind", "def"}]. Words that overlap (謫守, and 謫 in it) share a note."""
    # The pieces' characters end to end, as one text to look in, each knowing where it's from.
    where, text = [], []
    for p in found:
        for i, line in enumerate(p["lines"]):
            chars, at = han(line)
            where += [(p, i, start) for start in at]
            text.append(chars)
        where.append(None)
        text.append("\n")
    text = fold("".join(text))
    marks: dict[tuple, dict] = {}       # (piece, line, start, end) → its senses, by word
    for word, kind, meaning, quote in said:
        key = fold(word)
        clauses = {fold(BREAKS.sub("", quote)), *map(fold, BREAKS.split(quote))}
        for clause in (c for c in clauses if key in c and len(c) >= max(CLAUSE, len(key) + 1)):
            at = text.find(clause)
            while at >= 0:
                (p, line, start), (_, last_line, last) = (where[at + clause.find(key) + i] for i in (0, len(key) - 1))
                if line == last_line:           # the word as the piece writes it, the sense as the dictionary does
                    sense = {"word": p["lines"][line][start:last + 1], "kind": kind, "def": meaning}
                    marks.setdefault((id(p), line, start, last + 1), {}).setdefault(word, sense)
                at = text.find(clause, at + 1)
    by_piece = {id(p): p for p in found}
    for p in found:
        p["notes"] = []
    for (pid, line, start, end), words in sorted(marks.items(), key=lambda m: m[0][1:]):
        notes = by_piece[pid]["notes"]
        if notes and notes[-1]["line"] == line and start < notes[-1]["end"]:
            notes[-1]["end"] = max(end, notes[-1]["end"])
            notes[-1]["senses"] += words.values()
        else:
            notes.append({"line": line, "start": start, "end": end, "senses": list(words.values())})
    for p in found:
        for n in p["notes"]:
            n["senses"].sort(key=lambda s: -len(s["word"]))      # the phrase, then the words in it


def main(guwen_src, wiki_src, collection_src, dict_src, regular, semibold):
    found = list(pieces(guwen_src, wiki_src, collection_src))
    annotate(found, senses(dict_src))
    kept = [p for p in found if len("".join(p["lines"])) <= LONGEST and len(p["notes"]) >= FEWEST]
    (DATA / "passages.json").write_text(json.dumps(kept, ensure_ascii=False, indent=0), encoding="utf-8")
    cut(regular, semibold)
    print(f"{len(kept)} of {len(found)} pieces, {sum(len(p['notes']) for p in kept)} notes")


if __name__ == "__main__":
    main(*sys.argv[1:])
