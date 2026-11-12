"""Write data/prints.json: the Japanese and Chinese prints the Cleveland Museum of Art wrote
about, each with the museum's text, from its Open Access collection (CC0,
openaccess-api.clevelandart.org). From the API's list of each department's prints:

    API='https://openaccess-api.clevelandart.org/api/artworks/?cc0=1&has_image=1&type=Print&limit=1000'
    for d in Japanese Chinese; do curl "$API&department=$d%20Art" -o $d.json; done
    uv run python -I tools/add_prints.py Japanese.json Chinese.json
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from papers import clean, paragraphs  # noqa: E402

SOURCE = "Cleveland Museum of Art"
WORTH_READING = 250     # characters of the museum's text
SHAPES = (0.5, 2.0)     # width over height: a pillar print or a handscroll is too thin to show beside its text


def main(*lists):
    prints, told = [], set()
    for src in lists:
        for a in json.loads(pathlib.Path(src).read_text(encoding="utf-8"))["data"]:
            image = (a.get("images") or {}).get("web")
            about = a.get("description") or ""
            ratio = image and int(image["width"]) / int(image["height"])
            # One text is often given to every sheet of a set: the first sheet stands for it.
            if not image or len(about) < WORTH_READING or not SHAPES[0] <= ratio <= SHAPES[1] or about in told:
                continue
            told.add(about)
            aside = a.get("did_you_know") or ""
            prints.append({"id": a["accession_number"], "source": SOURCE, "title": clean(a["title"]),
                           "maker": clean(next((c["description"] for c in a["creators"]), "")),
                           "date": a.get("creation_date") or "", "technique": clean(a.get("technique")),
                           "text": paragraphs(re.split(r"(?:<br>\s*){2,}", about) + [aside] * (aside not in about)),
                           "url": a["url"], "figure": image["url"], "ratio": round(ratio, 3)})
    (ROOT / "data" / "prints.json").write_text(json.dumps(prints, ensure_ascii=False, indent=0), encoding="utf-8")
    print(len(prints), "prints")


if __name__ == "__main__":
    main(*sys.argv[1:])
