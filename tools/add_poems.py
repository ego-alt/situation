"""Write data/poems.json: 唐詩三百首 and 宋詞三百首 in Hong Kong's characters, then cut the typeface
they're set in down to what the page sets in it.

唐詩三百首 is the chinese-poetry collection's (MIT, github.com/chinese-poetry/chinese-poetry: its
蒙学/tangshisanbaishou.json): the anthology's choice, order and readings (南冠客思深, as it's
recited). But that text was converted from simplified, which guesses wherever a simplified
character has several traditional ones (明月鬆間照). Each of those is taken from 全唐詩's text of
the poem instead (the same collection's 全唐诗/唐诗三百首.json, typed in traditional), wherever
the two read alike. Where 全唐詩 reads otherwise, ERRATA sets it right by hand, and AS_QUAN names
the poems the first file hasn't whole. Its asides ("(X 一作：Y)") are left out and a title with a
variant ("早發白帝城 / 白帝下江陵") keeps the first.

宋詞三百首 is Wikisource's (zh.wikisource.org/wiki/宋詞三百首, 朱孝臧's edition of 1924, as its
wikitext: ?action=raw): a sentence to a line and an empty line between stanzas, prefaces left out.
One poem it takes from a page of its own (zh.wikisource.org/wiki/青玉案_(辛棄疾), the same way).

OpenCC then puts both in Hong Kong's forms (為, not 爲), traditional to traditional. The typeface
is 昭源宋體 (Chiron Sung HK, SIL Open Font License, github.com/chiron-fonts/chiron-sung-hk: its
STATIC_TTF/ChironSungHK-R.ttf and -SB.ttf):

    uv run --with opencc-python-reimplemented --with 'fonttools[woff]' python -I tools/add_poems.py \
        tangshisanbaishou.json 唐诗三百首.json 宋詞三百首.wiki 青玉案.wiki ChironSungHK-R.ttf ChironSungHK-SB.ttf
"""
import collections
import difflib
import functools
import json
import pathlib
import re
import sys

import opencc
from fontTools import subset

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FONTS = ROOT / "static" / "fonts"
# What else the page sets in it: the seal, the woodcut's names and moons (static/js/woodcut.js)
# and whose the notes are (static/js/dispatch.js).
SHOWN = "邸報" + "珠江口香港澳門廣州北織女牛郎" + "新月蛾眉上弦盈凸滿虧下殘" + "註釋：教育部《重編國語辭典修訂本》"

HAN = r"\u3400-\u9fff\U00020000-\U0003ffff"
ASIDES = re.compile(r"\s*[（(][^（()）]*[)）]")
MARKS = str.maketrans("“”‘’?!,;:︰﹕﹗﹖", "「」『』？！，；：：：！？")
RUN = 4             # characters: a run this long in two texts is a vote that they're the same one
ALIKE = 0.6         # of their characters in common: the same text, read a little differently
# The poems to take from 全唐詩 whole, by its title: the first file has a third of one, five poems
# under another's title, and another poem altogether.
AS_QUAN = {("贈衞八處士", "杜甫"): "贈衛八處士", ("長信怨", "王昌齡"): "相和歌辭 長信怨 二",
           ("宮詞", "顧況"): "宮詞五首 二"}
# What's left wrong, and right: a conversion's guess where 全唐詩 reads otherwise or titles the poem
# otherwise, 全唐詩's older forms in the poems taken from it, a slip in Wikisource's, and two
# couplets the first file ends on a comma.
ERRATA = {
    "流落徵南將": "流落征南將", "斑騅只系垂楊岸": "斑騅只繫垂楊岸", "日色慾盡": "日色欲盡", "嗟餘好古": "嗟余好古",
    "幽並客": "幽并客", "謝脁樓": "謝朓樓", "共裴回": "共徘徊", "寒鵶色": "寒鴉色", "問荅": "問答", "馮延已": "馮延巳",
    "疑是地上霜，": "疑是地上霜。", "兩小無嫌猜，": "兩小無嫌猜。",
}
# A poem on Wikisource's page: its heading (the title, the author), then its text.
POEM = re.compile(r"^=='''(.+?)'''（\[\[Author:[^|\]]*\|(.+?)\]\]）==$.*?<poem>(.*?)</poem>", re.M | re.S)
# The poem on a page of its own: its heading there, that heading as the others have it, what stands for its text.
OWN = ("==[[青玉案 (辛棄疾)]]==", "=='''青玉案'''（[[Author:辛棄疾|辛棄疾]]）==", "{{:青玉案 (辛棄疾)}}")

hk = opencc.OpenCC("t2hk").convert
_simplified = functools.cache(opencc.OpenCC("t2s").convert)
# The simplified characters that stand for several traditional ones: 发, for 發 and 髮.
SEVERAL = {line.split("\t")[0] for line in (pathlib.Path(opencc.__file__).parent / "dictionary" / "STCharacters.txt")
           .read_text(encoding="utf-8").splitlines() if " " in line}
# The forms of one character that OpenCC doesn't know to be one: 徧, for 遍.
SAME = str.maketrans("徧䫉祇祗煗煖剗敡徼儌", "遍貌只只暖暖鏟敭僥僥")


def fold(text: str) -> str:
    """A text with each character's forms as one (its simplified form): what two transcriptions are compared by."""
    return "".join(c if len(_simplified(c)) != 1 else _simplified(c) for c in text.translate(SAME))


def han(text: str) -> tuple[str, list[int]]:
    """A text's characters, and where each is: its marks and breaks aren't compared."""
    found = [(m.group(), m.start()) for m in re.finditer(f"[{HAN}]", text)]
    return "".join(c for c, _ in found), [at for _, at in found]


def differences(a: str, b: str) -> list[tuple]:
    """How `b` reads against `a`, both characters only: difflib's opcodes."""
    return difflib.SequenceMatcher(None, fold(a), fold(b), autojunk=False).get_opcodes()


def finder(texts: list[str]):
    """Which of `texts` (characters only) another transcribes: find(chars) is its place among them, or None."""
    folded = [fold(t) for t in texts]
    runs = collections.defaultdict(set)         # a run of characters → the texts it's in
    for n, t in enumerate(folded):
        for i in range(len(t) - RUN + 1):
            runs[t[i:i + RUN]].add(n)

    def find(chars: str) -> int | None:
        chars = fold(chars)
        votes = collections.Counter(n for i in range(len(chars) - RUN + 1) for n in runs.get(chars[i:i + RUN], ()))
        n = max(votes, key=votes.get, default=None)
        alike = n is not None and difflib.SequenceMatcher(None, chars, folded[n], autojunk=False).ratio() >= ALIKE
        return n if alike else None
    return find


def cut(regular, semibold) -> int:
    """Cut both weights down to SHOWN and what the poems and the passages (tools/add_passages.py)
    are written in; returns how many characters that is."""
    text = SHOWN + "".join((DATA / name).read_text(encoding="utf-8") for name in ("poems.json", "passages.json"))
    text = "".join(sorted({c for c in text if not c.isascii()}))
    for src, weight in ((regular, 400), (semibold, 600)):
        subset.main([src, f"--text={text}", "--flavor=woff2", "--layout-features=*",
                     f"--output-file={FONTS / f'chironsung-{weight}.woff2'}"])
    return len(text)


def settled(text: str, theirs: str) -> str:
    """`text` with each character the conversion guessed at as `theirs` has it, where the two read alike."""
    chars, at = han(text)
    out = list(text)
    for tag, i1, i2, j1, _ in differences(chars, theirs):
        if tag == "equal":
            for k in range(i1, i2):
                if _simplified(theirs[j1 + k - i1]) in SEVERAL:
                    out[at[k]] = theirs[j1 + k - i1]
    return "".join(out)


def tang(src, quan_src):
    """唐詩三百首's poems, in its order: the first file's text, its guessed characters settled by 全唐詩's."""
    quan = json.loads(pathlib.Path(quan_src).read_text(encoding="utf-8"))
    by_title = {q["title"]: q for q in quan}
    texts = [han("".join(q["paragraphs"]))[0] for q in quan]
    find = finder(texts)
    for group in json.loads(pathlib.Path(src).read_text(encoding="utf-8"))["content"]:
        for p in group["content"]:
            title = (p["subchapter"] or p["chapter"]).split(" / ")[0]
            text = ASIDES.sub("", "\n".join(p["paragraphs"]))
            if whole := AS_QUAN.get((hk(title), hk(p["author"]))):
                text = "\n".join(by_title[whole]["paragraphs"])
            elif (n := find(han(text)[0])) is not None:
                title, text = settled(title, han(quan[n]["title"])[0]), settled(text, texts[n])
            yield {"book": "唐詩三百首", "title": title, "author": p["author"], "form": group["type"],
                   "lines": text.split("\n")}


def song(src, own_src):
    """宋詞三百首's poems, in its order: Wikisource's text, a sentence to a line."""
    page = pathlib.Path(src).read_text(encoding="utf-8").replace("-{", "").replace("}-", "")
    own = pathlib.Path(own_src).read_text(encoding="utf-8")
    own = re.search(r"<onlyinclude>(.*?)</onlyinclude>", own, re.S).group(1)
    heading, headed, stands = OWN
    page = page.replace(heading, headed).replace(stands, own)
    for title, author, text in POEM.findall(page):
        lines = []
        for stanza in re.split(r"\n\s*\n|　　", ASIDES.sub("", text).strip()):
            lines += re.findall(r".+?[。？！][」』]?|.+$", re.sub(r"\s", "", stanza)) + [""]
        yield {"book": "宋詞三百首", "title": title, "author": author, "form": "詞", "lines": lines[:-1]}


def main(tang_src, quan_src, song_src, own_src, regular, semibold):
    poems = [{k: [hk(line.translate(MARKS)) for line in v] if k == "lines" else hk(v) for k, v in p.items()}
             for p in (*tang(tang_src, quan_src), *song(song_src, own_src))]
    text = json.dumps(poems, ensure_ascii=False, indent=0)
    for wrong, right in ERRATA.items():
        assert wrong in text, wrong
        text = text.replace(wrong, right)
    (DATA / "poems.json").write_text(text, encoding="utf-8")
    print(f"{len(poems)} poems, {cut(regular, semibold)} characters")


if __name__ == "__main__":
    main(*sys.argv[1:])
