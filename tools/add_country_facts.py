"""Write data/country_facts.json: each country's population, area and main
language, from GeoNames' countryInfo.txt (CC BY 4.0, download.geonames.org/export/dump):

    python -I tools/add_country_facts.py countryInfo.txt
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
LANGUAGES = 1          # the main language (later ones can be minor: Brazil lists Spanish and English)


def main(src):
    facts = {}
    for line in pathlib.Path(src).read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("\t")
        codes = [code.split("-")[0] for code in f[15].split(",") if code]
        facts[f[0]] = {"population": int(f[7] or 0) or None, "area": round(float(f[6] or 0)) or None,
                       "languages": list(dict.fromkeys(codes))[:LANGUAGES]}
    (ROOT / "data" / "country_facts.json").write_text(json.dumps(facts, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(facts)} countries")


if __name__ == "__main__":
    main(sys.argv[1])
