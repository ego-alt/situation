"""Write data/poems.json: 唐詩三百首 and 宋詞三百首 in traditional characters, from the
chinese-poetry collection (MIT, github.com/chinese-poetry/chinese-poetry): its
蒙学/tangshisanbaishou.json and 宋词/宋词三百首.json. Both are converted with OpenCC to Hong
Kong's forms (為, not 爲): the Song file from simplified, the Tang from older traditional. A
title with a variant ("早發白帝城 / 白帝下江陵") keeps the first. Then it cuts the poem's typeface,
昭源宋體 (Chiron Sung HK, SIL Open Font License, github.com/chiron-fonts/chiron-sung-hk: its
STATIC_TTF/ChironSungHK-R.ttf and -SB.ttf), down to the characters the poems and the page use:

    uv run --with opencc-python-reimplemented --with 'fonttools[woff]' python -I tools/add_poems.py \
        tangshisanbaishou.json 宋词三百首.json ChironSungHK-R.ttf ChironSungHK-SB.ttf
"""
import json
import pathlib
import sys

from fontTools import subset
from opencc import OpenCC

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONTS = ROOT / "static" / "fonts"
# What else the page sets in it: the section head's 唐詩三百首 or 宋詞三百首, the seal, and the
# woodcut's names and moons (static/js/woodcut.js).
SHOWN = "三百首" + "邸報" + "珠江口香港澳門廣州北織女牛郎" + "新月蛾眉上弦盈凸滿虧下殘"


def main(tang_src, song_src, regular, semibold):
    poems = []
    hk = OpenCC("t2hk").convert
    for group in json.loads(pathlib.Path(tang_src).read_text(encoding="utf-8"))["content"]:
        for p in group["content"]:
            title = (p["subchapter"] or p["chapter"]).split(" / ")[0]
            poems.append({"book": "唐詩", "title": hk(title), "author": hk(p["author"]), "form": hk(group["type"]),
                          "lines": [hk(line) for line in p["paragraphs"]]})
    hk = OpenCC("s2hk").convert
    for p in json.loads(pathlib.Path(song_src).read_text(encoding="utf-8")):
        poems.append({"book": "宋詞", "title": hk(p["rhythmic"]), "author": hk(p["author"]),
                      "form": "詞", "lines": [hk(line) for line in p["paragraphs"]]})
    (ROOT / "data" / "poems.json").write_text(json.dumps(poems, ensure_ascii=False, indent=0), encoding="utf-8")
    text = SHOWN + "".join(p["book"] + p["title"] + p["author"] + p["form"] + "".join(p["lines"]) for p in poems)
    for src, weight in ((regular, 400), (semibold, 600)):
        subset.main([src, f"--text={text}", "--flavor=woff2", "--layout-features=*",
                     f"--output-file={FONTS / f'chironsung-{weight}.woff2'}"])
    print(f"{len(poems)} poems, {len(set(text))} characters")


if __name__ == "__main__":
    main(*sys.argv[1:])
