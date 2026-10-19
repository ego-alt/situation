"""Build static/map/world.json and data/countries.json from Natural Earth's
1:110m countries (public domain). Run once, or when changing the map:

    python -I tools/build_map.py ne_110m_admin_0_countries.geojson
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from projection import HEIGHT, WIDTH, project  # noqa: E402


def ring_path(ring):
    pts, prev = [], None
    for lon, lat in ring:
        lat = max(lat, -60.0)
        p = project(lon, lat)
        if p != prev:
            pts.append(p)
            prev = p
    if len(pts) < 3:
        return ""
    return "M" + "L".join(f"{x:g},{y:g}" for x, y in pts) + "Z"


def main(src):
    features = json.load(open(src, encoding="utf-8"))["features"]
    shapes, countries = [], []
    for f in features:
        p = f["properties"]
        if p["ADMIN"] == "Antarctica":
            continue
        iso2 = p["ISO_A2_EH"] if p["ISO_A2"] in ("-99", None) else p["ISO_A2"]
        iso2 = {"CN-TW": "TW"}.get(iso2, iso2)   # Natural Earth codes Taiwan by its claim
        polys = f["geometry"]["coordinates"]
        if f["geometry"]["type"] == "Polygon":
            polys = [polys]
        d = "".join(ring_path(ring) for poly in polys for ring in poly)
        shapes.append({"iso": iso2, "name": p["NAME"], "d": d})
        names = {p[k] for k in ("NAME", "NAME_LONG", "ADMIN", "NAME_EN", "FORMAL_EN", "NAME_ALT") if p.get(k)}
        countries.append({
            "iso": iso2, "iso3": p["ISO_A3"] if p["ISO_A3"] != "-99" else p.get("ADM0_A3"),
            "name": p["NAME"], "names": sorted(names), "lon": p["LABEL_X"], "lat": p["LABEL_Y"],
            "continent": p["CONTINENT"], "subregion": p["SUBREGION"],
        })
    def line(points):
        return "M" + "L".join(f"{x:g},{y:g}" for x, y in (project(lon, lat) for lon, lat in points))

    graticule = "".join(line([(lon, lat) for lat in range(-60, 91, 2)]) for lon in range(-180, 181, 30))
    graticule += "".join(line([(lon, lat) for lon in range(-180, 181, 2)]) for lat in (-30, 0, 30, 60))
    outline = line([(-180, lat) for lat in range(-60, 91, 2)] + [(180, lat) for lat in range(90, -61, -2)]) + "Z"
    (ROOT / "static/map/world.json").write_text(json.dumps(
        {"width": WIDTH, "height": HEIGHT, "outline": outline, "graticule": graticule, "countries": shapes},
        separators=(",", ":")))
    (ROOT / "data/countries.json").write_text(json.dumps(countries, ensure_ascii=False, indent=0))
    print(len(shapes), "shapes;", round((ROOT / "static/map/world.json").stat().st_size / 1024), "KB")


if __name__ == "__main__":
    main(sys.argv[1])
