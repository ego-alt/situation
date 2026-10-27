"""Situation's sizes come from tokens.css: no stylesheet sets a raw font size, so
the type scale can't creep back to sizes a pixel apart (dashboard/docs/ui-spec.md)."""
import pathlib
import re

CSS = pathlib.Path(__file__).resolve().parent.parent / "static" / "css"
SIZE = r"var\(--text-[\w-]+\)|inherit|0|[\d.]+(em|%)"   # a token, or relative to the text around it


def _sizes(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    yield from (v.strip() for v in re.findall(r"font-size:\s*([^;}!]+)", css))
    for value in re.findall(r"(?<![\w-])font:\s*([^;}]+)", css):   # the shorthand's size: the token before any family
        if value.strip() != "inherit":
            m = re.search(r"(var\(--text-[\w-]+\)|[\d.]+(?:px|rem|em|%))", value)
            if m:
                yield m.group(1)


def test_font_sizes_come_from_tokens():
    raw = [f"{p.name}: {v}" for p in sorted(CSS.glob("*.css")) if p.name != "tokens.css"
           for v in _sizes(p.read_text()) if not re.fullmatch(SIZE, v)]
    assert not raw, "use a --text-* token from tokens.css:\n" + "\n".join(raw)


def test_type_scale_is_small():
    assert len(re.findall(r"--text-[\w-]+:", (CSS / "tokens.css").read_text())) <= 5
