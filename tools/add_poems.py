"""Write data/poems.json: 唐詩三百首 and 宋詞三百首 in traditional characters, from the
chinese-poetry collection (MIT, github.com/chinese-poetry/chinese-poetry): its
蒙学/tangshisanbaishou.json and 宋词/宋词三百首.json. Both are converted with OpenCC to Hong
Kong's forms (為, not 爲): the Song file from simplified, the Tang from older traditional. A
title with a variant ("早發白帝城 / 白帝下江陵") keeps the first:

    uv run --with opencc-python-reimplemented python -I tools/add_poems.py tangshisanbaishou.json 宋词三百首.json
"""
import json
import pathlib
import sys

from opencc import OpenCC

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main(tang_src, song_src):
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
    print(f"{len(poems)} poems")


if __name__ == "__main__":
    main(*sys.argv[1:])
