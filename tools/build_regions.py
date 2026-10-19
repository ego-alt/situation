"""Build the zoomed-in map and the gazetteer of places inside countries.

    uv run --no-project --with pyshp python -I tools/build_regions.py <natural-earth dir> <cities15000.txt>

Inputs (both free to use):
  - Natural Earth 1:10m countries and states/provinces (public domain), unzipped
    into <natural-earth dir>/ne_10m_admin_0_countries/ and .../ne_10m_admin_1_states_provinces/
  - GeoNames cities15000.txt, every town over 15,000 people (CC BY 4.0, geonames.org)

Writes:
  static/map/regions/<ISO>.json   one per country, loaded when you zoom in: the
                                  detailed outline, its regions, region labels
                                  and its biggest cities
  data/gazetteer.json             cities and regions, for finding places in articles
"""
import json
import pathlib
import sys

import shapefile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from projection import _X_MAX, _Y_TOP, SCALE, _raw  # noqa: E402


def project(lon, lat):
    """projection.project without its rounding: the zoomed map needs the detail."""
    x, y = _raw(lon, lat)
    return (x + _X_MAX) * SCALE, (_Y_TOP - y) * SCALE

TOLERANCE = 0.03      # map units (the world is 1000 wide): well under a pixel at the closest zoom
MIN_RING = 0.06       # drop islets smaller than this across
CITIES_SHOWN = 12     # reference cities per country on the zoomed map

# Region names that are ordinary words in an English headline.
GENERIC = {
    "central", "northern", "southern", "eastern", "western", "north", "south", "east", "west", "capital",
    "coast", "centre", "center", "islands", "island", "lakes", "upper", "lower", "federal", "city", "border",
    "north east", "north west", "south east", "south west", "northeast", "northwest", "southeast", "southwest",
    "interior", "highlands", "lowlands", "plateau", "valley", "delta", "mountain", "mountains", "national",
    "metropolitan", "riviera", "atlantic", "pacific", "littoral", "nord", "sud", "est", "ouest",
}
# City names that are ordinary words in an English headline.
COMMON = {
    "Police", "Mobile", "Reading", "Bath", "Nice", "Split", "Hope", "Union", "Victoria", "Independence",
    "Liberty", "Florida", "Georgia", "Jordan", "March", "May", "Mercedes", "Florence", "Of", "Sale", "Deal",
    "Worth", "Concord", "Providence", "Paradise", "Freedom", "Unity", "Progreso", "Trinidad", "Santa",
    "Central", "Bell", "Orange", "Marina", "Gold Coast", "Many", "Opportunity", "Enterprise", "Normal",
}


def iso_of(props, a2, fallback):
    iso = props.get(a2)
    if iso in (None, "-99", "-1"):
        iso = props.get(fallback)
    return {"CN-TW": "TW"}.get(iso, iso)


def simplify(points, tol):
    """Douglas-Peucker, iterative. A closed ring is split in two first: its
    ends are the same point, which would leave nothing to measure against."""
    if len(points) < 4:
        return points
    if points[0] == points[-1]:
        mid = len(points) // 2
        return simplify(points[: mid + 1], tol)[:-1] + simplify(points[mid:], tol)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = points[a], points[b]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5 or 1e-12
        far, idx = 0.0, None
        for i in range(a + 1, b):
            px, py = points[i]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > far:
                far, idx = d, i
        if idx is not None and far > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep, strict=True) if k]


def rings(shape):
    """A shape's rings, projected and simplified, with their bounding boxes."""
    parts = list(shape.parts) + [len(shape.points)]
    out = []
    for s, e in zip(parts, parts[1:], strict=False):
        pts = [project(lon, max(lat, -60.0)) for lon, lat in shape.points[s:e]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if not pts or max(xs) - min(xs) < MIN_RING and max(ys) - min(ys) < MIN_RING:
            continue
        pts = simplify(pts, TOLERANCE)
        if len(pts) >= 3:
            out.append((pts, (min(xs), min(ys), max(xs), max(ys))))
    return out


def path(rs):
    return "".join("M" + "L".join(f"{x:.2f},{y:.2f}".replace(".00", "") for x, y in pts) + "Z" for pts, _ in rs)


def focus(rings_):
    """The mainland: the biggest ring, and whatever lies close to it (not
    French Guiana for France, or Hawaii for the US)."""
    big = max((b for _, b in rings_), key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
    w, h = big[2] - big[0], big[3] - big[1]
    near = [b for _, b in rings_ if big[0] - w * 0.25 <= (b[0] + b[2]) / 2 <= big[2] + w * 0.25
            and big[1] - h * 0.25 <= (b[1] + b[3]) / 2 <= big[3] + h * 0.25]
    return bbox(near)


def bbox(boxes):
    return [round(min(b[0] for b in boxes), 2), round(min(b[1] for b in boxes), 2),
            round(max(b[2] for b in boxes), 2), round(max(b[3] for b in boxes), 2)]


def reader(base, name):
    r = shapefile.Reader(str(base / name / name), encoding="utf-8")
    fields = [f[0] for f in r.fields[1:]]
    for sr in r.iterShapeRecords():
        yield sr.shape, dict(zip(fields, sr.record, strict=True))


def main(ne_dir, cities_txt):
    base = pathlib.Path(ne_dir)
    countries: dict[str, dict] = {}
    for shape, p in reader(base, "ne_10m_admin_0_countries"):
        iso = iso_of(p, "ISO_A2", "ISO_A2_EH")
        if not iso or iso == "AQ" or p.get("ADMIN") == "Antarctica":
            continue
        rs = rings(shape)
        if rs:
            c = countries.setdefault(iso, {"iso": iso, "outline": [], "regions": [], "labels": [], "cities": []})
            c["outline"] += rs

    gazetteer = []
    for shape, p in reader(base, "ne_10m_admin_1_states_provinces"):
        iso = iso_of(p, "iso_a2", "iso_a2")
        if iso not in countries:
            continue
        rs = rings(shape)
        name = p.get("name_en") or p["name"]
        if rs:
            countries[iso]["regions"].append({"name": name, "d": path(rs)})
        if p.get("latitude") is not None:
            x, y = project(p["longitude"], p["latitude"])
            countries[iso]["labels"].append({"name": name, "x": round(x, 2), "y": round(y, 2)})
            names = {p["name"], p.get("name_en") or ""} | set((p.get("name_alt") or "").split("|"))
            for n in sorted(n.strip() for n in names if n and n.strip()):
                if n.lower() not in GENERIC and len(n) >= 3 and "Oblast'" not in n:
                    gazetteer.append([n, iso, round(p["latitude"], 3), round(p["longitude"], 3), 0, "region"])

    # GeoNames: geonameid, name, asciiname, alternatenames, lat, lon, class, code, country, ..., population (14)
    by_country: dict[str, list] = {}
    for line in open(cities_txt, encoding="utf-8"):
        f = line.rstrip("\n").split("\t")
        name, ascii_name, lat, lon, iso, pop = f[1], f[2], float(f[4]), float(f[5]), f[8], int(f[14] or 0)
        iso = {"XK": "XK"}.get(iso, iso)
        for n in {name, ascii_name}:
            if n not in COMMON and len(n) >= 3:
                gazetteer.append([n, iso, round(lat, 3), round(lon, 3), pop, "city"])
        by_country.setdefault(iso, []).append((pop, name, lat, lon))
    for iso, cities in by_country.items():
        if iso in countries:
            for pop, name, lat, lon in sorted(cities, reverse=True)[:CITIES_SHOWN]:
                x, y = project(lon, lat)
                countries[iso]["cities"].append({"name": name, "x": round(x, 2), "y": round(y, 2), "pop": pop})

    out = ROOT / "static/map/regions"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    total = 0
    for iso, c in countries.items():
        doc = {"iso": iso, "bbox": bbox([b for _, b in c["outline"]]), "focus": focus(c["outline"]),
               "outline": path(c["outline"]),
               "regions": c["regions"], "labels": c["labels"], "cities": c["cities"]}
        target = out / f"{iso}.json"
        target.write_text(json.dumps(doc, separators=(",", ":"), ensure_ascii=False))
        total += target.stat().st_size
    gazetteer.sort(key=lambda g: (g[0], -g[4]))
    (ROOT / "data/gazetteer.json").write_text(
        "[\n" + ",\n".join(json.dumps(g, ensure_ascii=False) for g in gazetteer) + "\n]\n")
    print(f"{len(countries)} countries, {round(total / 1024)} KB; gazetteer {len(gazetteer)} names")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
