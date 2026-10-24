"""Where a story happens: an offline lookup of countries (names, demonyms,
capitals, big cities and leaders) in headlines and summaries.

The place mentioned most, weighted toward the headline, wins; ties go to the
first one mentioned. Places that are named mostly as actors ("Washington
says…", "Beijing warns…") count less than places where things happen."""
import json
import math
import pathlib
import re
from collections import Counter
from functools import lru_cache

from projection import project

_DATA = pathlib.Path(__file__).resolve().parent / "data" / "countries.json"

# What a country's people or things are called (comma-separated alternates).
DEMONYMS = {
    "AF": "Afghan", "AL": "Albanian", "DZ": "Algerian", "AO": "Angolan", "AR": "Argentine, Argentinian",
    "AM": "Armenian", "AU": "Australian", "AT": "Austrian", "AZ": "Azerbaijani", "BD": "Bangladeshi",
    "BY": "Belarusian", "BE": "Belgian", "BO": "Bolivian", "BA": "Bosnian", "BR": "Brazilian",
    "BG": "Bulgarian", "MM": "Burmese", "KH": "Cambodian", "CM": "Cameroonian", "CA": "Canadian",
    "CL": "Chilean", "CN": "Chinese", "CO": "Colombian", "CD": "Congolese", "HR": "Croatian",
    "CU": "Cuban", "CY": "Cypriot", "CZ": "Czech", "DK": "Danish", "EC": "Ecuadorian", "EG": "Egyptian",
    "EE": "Estonian", "ET": "Ethiopian", "FI": "Finnish", "FR": "French", "GE": "Georgian",
    "DE": "German", "GH": "Ghanaian", "GR": "Greek", "GT": "Guatemalan", "HT": "Haitian",
    "HU": "Hungarian", "IS": "Icelandic", "IN": "Indian", "ID": "Indonesian", "IR": "Iranian",
    "IQ": "Iraqi", "IE": "Irish", "IL": "Israeli", "IT": "Italian", "JP": "Japanese", "JO": "Jordanian",
    "KZ": "Kazakh", "KE": "Kenyan", "KP": "North Korean", "KR": "South Korean", "XK": "Kosovar",
    "KW": "Kuwaiti", "KG": "Kyrgyz", "LA": "Lao, Laotian", "LV": "Latvian", "LB": "Lebanese",
    "LY": "Libyan", "LT": "Lithuanian", "MY": "Malaysian", "ML": "Malian", "MX": "Mexican",
    "MD": "Moldovan", "MN": "Mongolian", "ME": "Montenegrin", "MA": "Moroccan", "MZ": "Mozambican",
    "NP": "Nepali, Nepalese", "NL": "Dutch", "NZ": "New Zealander", "NI": "Nicaraguan", "NE": "Nigerien",
    "NG": "Nigerian", "NO": "Norwegian", "PK": "Pakistani", "PS": "Palestinian", "PA": "Panamanian",
    "PY": "Paraguayan", "PE": "Peruvian", "PH": "Filipino, Philippine", "PL": "Polish", "PT": "Portuguese",
    "QA": "Qatari", "RO": "Romanian", "RU": "Russian", "RW": "Rwandan", "SA": "Saudi", "SN": "Senegalese",
    "RS": "Serbian", "SG": "Singaporean", "SK": "Slovak", "SI": "Slovenian", "SO": "Somali",
    "ZA": "South African", "SS": "South Sudanese", "ES": "Spanish", "LK": "Sri Lankan", "SD": "Sudanese",
    "SE": "Swedish", "CH": "Swiss", "SY": "Syrian", "TW": "Taiwanese", "TJ": "Tajik", "TZ": "Tanzanian",
    "TH": "Thai", "TN": "Tunisian", "TR": "Turkish", "TM": "Turkmen", "UG": "Ugandan", "UA": "Ukrainian",
    "AE": "Emirati", "GB": "British", "US": "American", "UY": "Uruguayan", "UZ": "Uzbek",
    "VE": "Venezuelan", "VN": "Vietnamese", "YE": "Yemeni", "ZM": "Zambian", "ZW": "Zimbabwean",
}

# Short or common forms the country table doesn't carry, and groups or
# institutions that stand for one country (comma-separated).
ALIASES = {
    "US": "US, U.S., USA, America, Pentagon, White House",
    "GB": "UK, U.K., Britain, Downing Street",
    "RU": "Kremlin",
    "PS": "Gaza, West Bank, Palestine, Hamas",
    "YE": "Houthi, Houthis",
    "LB": "Hezbollah",
    "AF": "Taliban",
    "VA": "Vatican, Pope",
    "CD": "DRC",
    "AE": "UAE",
    "MM": "Myanmar",
}

# Capitals, one per country. Names that are also common elsewhere (Victoria,
# Kingston, San José, Georgetown) are left out.
CAPITALS = {
    "AF": "Kabul", "AL": "Tirana", "DZ": "Algiers", "AO": "Luanda", "AR": "Buenos Aires", "AM": "Yerevan",
    "AU": "Canberra", "AT": "Vienna", "AZ": "Baku", "BD": "Dhaka", "BY": "Minsk", "BE": "Brussels",
    "BO": "La Paz", "BA": "Sarajevo", "BR": "Brasília, Brasilia", "BG": "Sofia", "MM": "Naypyidaw",
    "KH": "Phnom Penh", "CM": "Yaoundé", "CA": "Ottawa", "CL": "Santiago", "CN": "Beijing", "CO": "Bogotá, Bogota",
    "CD": "Kinshasa", "HR": "Zagreb", "CU": "Havana", "CY": "Nicosia", "CZ": "Prague", "DK": "Copenhagen",
    "EC": "Quito", "EG": "Cairo", "EE": "Tallinn", "ET": "Addis Ababa", "FI": "Helsinki", "FR": "Paris",
    "GE": "Tbilisi", "DE": "Berlin", "GH": "Accra", "GR": "Athens", "GT": "Guatemala City", "HT": "Port-au-Prince",
    "HU": "Budapest", "IS": "Reykjavik", "IN": "New Delhi", "ID": "Jakarta", "IR": "Tehran", "IQ": "Baghdad",
    "IE": "Dublin", "IL": "Jerusalem", "IT": "Rome", "JP": "Tokyo", "JO": "Amman", "KZ": "Astana",
    "KE": "Nairobi", "KP": "Pyongyang", "KR": "Seoul", "XK": "Pristina", "KW": "Kuwait City", "KG": "Bishkek",
    "LA": "Vientiane", "LV": "Riga", "LB": "Beirut", "LY": "Tripoli", "LT": "Vilnius", "MY": "Kuala Lumpur",
    "ML": "Bamako", "MX": "Mexico City", "MD": "Chisinau, Chișinău", "MN": "Ulaanbaatar", "ME": "Podgorica",
    "MA": "Rabat", "MZ": "Maputo", "NP": "Kathmandu", "NL": "Amsterdam, The Hague", "NZ": "Wellington",
    "NI": "Managua", "NE": "Niamey", "NG": "Abuja", "NO": "Oslo", "PK": "Islamabad", "PA": "Panama City",
    "PY": "Asunción, Asuncion", "PE": "Lima", "PH": "Manila", "PL": "Warsaw", "PT": "Lisbon", "QA": "Doha",
    "RO": "Bucharest", "RU": "Moscow", "RW": "Kigali", "SA": "Riyadh", "SN": "Dakar", "RS": "Belgrade",
    "SG": "Singapore", "SK": "Bratislava", "SI": "Ljubljana", "SO": "Mogadishu", "ZA": "Pretoria",
    "SS": "Juba", "ES": "Madrid", "LK": "Colombo", "SD": "Khartoum", "SE": "Stockholm", "CH": "Bern",
    "SY": "Damascus", "TW": "Taipei", "TJ": "Dushanbe", "TZ": "Dodoma", "TH": "Bangkok", "TN": "Tunis",
    "TR": "Ankara", "TM": "Ashgabat", "UG": "Kampala", "UA": "Kyiv, Kiev", "AE": "Abu Dhabi", "GB": "London",
    "US": "Washington", "UY": "Montevideo", "UZ": "Tashkent", "VE": "Caracas", "VN": "Hanoi", "YE": "Sanaa, Aden",
    "ZM": "Lusaka", "ZW": "Harare", "SV": "San Salvador", "HN": "Tegucigalpa", "DO": "Santo Domingo",
    "OM": "Muscat", "ER": "Asmara", "DJ": "Djibouti", "TD": "N'Djamena",
    "CF": "Bangui", "CG": "Brazzaville", "GA": "Libreville", "BF": "Ouagadougou", "GN": "Conakry",
    "SL": "Freetown", "CI": "Abidjan, Yamoussoukro", "MG": "Antananarivo", "MW": "Lilongwe", "NA": "Windhoek",
    "BW": "Gaborone", "MK": "Skopje", "LU": "Luxembourg", "BT": "Thimphu",
}

# Big cities that are often where the news happens (not capitals).
CITIES = {
    "US": "New York, Los Angeles, Chicago", "GB": "Manchester, Birmingham", "FR": "Marseille, Lyon",
    "DE": "Munich, Hamburg, Frankfurt", "IT": "Milan, Naples", "ES": "Barcelona", "TR": "Istanbul",
    "RU": "St Petersburg, Saint Petersburg", "UA": "Kharkiv, Odesa, Odessa", "IN": "Mumbai, Delhi, Kolkata, Bengaluru",
    "PK": "Karachi, Lahore", "CN": "Shanghai, Shenzhen, Guangzhou, Wuhan", "JP": "Osaka", "AU": "Sydney, Melbourne",
    "CA": "Toronto, Montreal, Vancouver", "BR": "São Paulo, Sao Paulo, Rio de Janeiro", "NG": "Lagos",
    "AE": "Dubai", "IL": "Tel Aviv", "SA": "Jeddah", "SY": "Aleppo", "SD": "Darfur, El Fasher",
    "ZA": "Johannesburg, Cape Town", "EG": "Alexandria", "ID": "Bali", "MX": "Guadalajara", "VN": "Ho Chi Minh City",
}

# Heads of state and government, as of October 2026: review when they change.
# Full names, or surnames that don't mean anything else.
LEADERS = {
    "US": "Trump", "GB": "Starmer", "FR": "Macron", "DE": "Merz", "IT": "Meloni", "ES": "Pedro Sánchez",
    "CA": "Carney", "AU": "Albanese", "JP": "Takaichi", "IN": "Modi", "CN": "Xi Jinping", "RU": "Putin",
    "UA": "Zelensky, Zelenskyy", "IL": "Netanyahu", "TR": "Erdogan, Erdoğan", "BR": "Lula", "MX": "Sheinbaum",
    "AR": "Milei", "KR": "Lee Jae-myung", "KP": "Kim Jong Un, Kim Jong-un", "TW": "Lai Ching-te",
    "HK": "John Lee", "SA": "Mohammed bin Salman", "PL": "Tusk", "ZA": "Ramaphosa", "NG": "Tinubu",
    "EG": "Sisi", "BY": "Lukashenko", "SY": "al-Sharaa", "PS": "Abbas", "VA": "Pope Leo",
}

# Places that aren't countries in the map's table but matter here.
EXTRA = {
    "HK": ("Hong Kong", 22.32, 114.17, "Hongkongers, Kowloon"),
    "VA": ("Vatican", 41.90, 12.45, ""),
    "SG": ("Singapore", 1.35, 103.82, ""),
}

# Named as actors more than as places ("Washington says", "Trump warns"): count them less.
ACTOR_WORDS = {"Washington", "White House", "Pentagon", "Kremlin", "Beijing", "Moscow", "Brussels", "Trump"}
# Country names that are often something else (a US state, a first name, a word).
AMBIGUOUS = {"Georgia", "Jordan", "Chad"}


@lru_cache(maxsize=1)
def _table():
    countries = {c["iso"]: c for c in json.loads(_DATA.read_text())}
    patterns = []
    split = lambda text: [n.strip() for n in (text or "").split(",") if n.strip()]  # noqa: E731
    tables = (("demonym", DEMONYMS), ("alias", ALIASES), ("capital", CAPITALS), ("city", CITIES), ("leader", LEADERS))
    for iso, c in countries.items():
        found = {n: "name" for n in c["names"]}
        for kind, table in tables:
            for n in split(table.get(iso)):
                found.setdefault(n, kind)
        patterns += [(n, iso, kind) for n, kind in found.items() if len(n) >= 2]
    for iso, (name, lat, lon, more) in EXTRA.items():
        countries.setdefault(iso, {"iso": iso, "name": name, "lat": lat, "lon": lon})
        patterns += [(n, iso, "name") for n in [name, *split(more)]]
        patterns += [(n, iso, "alias") for n in split(ALIASES.get(iso))]
        patterns += [(n, iso, "leader") for n in split(LEADERS.get(iso))]
    # Longest first, so "South Sudan" wins over "Sudan" and "Hong Kong" over "Kong".
    patterns.sort(key=lambda p: -len(p[0]))
    regex = re.compile(r"\b(" + "|".join(re.escape(p[0]) for p in patterns) + r")\b")
    lookup = {p[0]: p[1] for p in reversed(patterns)}
    kinds = {p[0]: p[2] for p in reversed(patterns)}
    return countries, regex, lookup, kinds


def mentions(text: str) -> list[tuple[str, str]]:
    """Each country the text names, in order, with how it's named: (iso, kind),
    kind one of name, demonym, alias, capital, city, leader, actor (a capital
    or institution named as the one acting: "Washington says") or ambiguous
    (a country name that's often something else: Georgia, Jordan)."""
    _, regex, lookup, kinds = _table()
    out = []
    for m in regex.finditer(text or ""):
        word = m.group(1)
        if word in lookup:
            kind = "actor" if word in ACTOR_WORDS else "ambiguous" if word in AMBIGUOUS else kinds[word]
            out.append((lookup[word], kind))
    return out


def places(text: str) -> Counter:
    found = Counter()
    for iso, kind in mentions(text):
        found[iso] += 0.4 if kind == "actor" else 0.3 if kind == "ambiguous" else 1.0
    return found


def locate(articles: list[dict]) -> dict | None:
    """The main place of a group of articles: {"iso", "name", "lat", "lon", "x", "y"}.
    With a model learned from your labels (learn.py) it chooses; otherwise the
    country named most, headlines counting three times."""
    import learn
    model = learn.load("place")
    if model:
        iso = learn.choose_place(model, articles)
    else:
        total = Counter()
        for a in articles:
            total.update({k: v * 3 for k, v in places(a["title"]).items()})
            total.update(places(a.get("summary") or ""))
        iso = total.most_common(1)[0][0] if total else None
    if iso is None:
        return None
    c = _table()[0][iso]
    x, y = project(c["lon"], c["lat"])
    return {"iso": iso, "name": c["name"], "lat": c["lat"], "lon": c["lon"], "x": x, "y": y}


# ---------- places inside countries: cities and regions ----------

_GAZETTEER = pathlib.Path(__file__).resolve().parent / "data" / "gazetteer.json"
# Small words that can sit inside a place's name ("Rio de Janeiro", "Bandar Seri Begawan").
_JOINERS = {"de", "da", "do", "del", "la", "le", "el", "al", "of", "on", "upon", "sur", "am", "an", "der"}
BIG_CITY = 1_000_000    # a city this big counts even when the article doesn't name its country
# "Donetsk region", "Kherson oblast": the region, not the city of that name.
_REGION_WORDS = {"region", "oblast", "province", "state", "county", "prefecture", "governorate", "department"}


@lru_cache(maxsize=1)
def _gazetteer():
    """{name: [(iso, lat, lon, population, kind), ...]}."""
    names: dict[str, list] = {}
    for name, iso, lat, lon, pop, kind in json.loads(_GAZETTEER.read_text()):
        names.setdefault(name, []).append((iso, lat, lon, pop, kind))
    return names


# A run of capitalised words, joined only by spaces (punctuation ends a run),
# with the small joining words allowed inside: "Rio de Janeiro", "Johns Hopkins University".
_RUN = re.compile(r"[A-ZÀ-Þ][\w'’.-]*(?:\s+(?:(?:" + "|".join(sorted(_JOINERS)) + r")\s+)?[A-ZÀ-Þ][\w'’.-]*)*")


def _phrases(text: str):
    """(phrase, next word) for each run of capitalised words that is a place.

    The place has to be the whole run, so a place inside a longer name
    doesn't count: Real Madrid, Manchester City, the New York Times, Vladimir
    Putin. A trailing "Region" or "Oblast" is allowed and kept as a hint."""
    names = _gazetteer()
    for m in _RUN.finditer(text or ""):
        words = [w.rstrip(".").removesuffix("'s").removesuffix("’s") for w in m.group(0).split()]
        after = text[m.end():m.end() + 30].split()
        after = after[0].lower().strip(".,;:") if after else ""
        if len(words) > 1 and words[-1].lower() in _REGION_WORDS:
            after = words.pop().lower()
        phrase = " ".join(words)
        if words[0] in ("St", "Ste") and phrase not in names:
            phrase = phrase.replace("St ", "Saint ", 1).replace("Ste ", "Sainte ", 1)   # St Petersburg
        if phrase in names:
            yield phrase, after


def spots(text: str) -> list[dict]:
    """The cities and regions a piece of text names: [{"name", "iso", "kind", "lat", "lon"}].

    A name shared by several places counts only for the place whose country
    the text also names ("Pokrovsk ... Ukraine"), or for a city of a million
    or more. Countries, and capitals named as actors ("Moscow says"), aren't
    spots: they're already the country pin."""
    countries, _, lookup, _ = _table()
    named = set(places(text))
    names = _gazetteer()
    found, seen = [], set()
    for phrase, after in _phrases(text):
        if phrase in ACTOR_WORDS or (phrase in lookup and phrase not in _capitals()):
            continue                       # a country, demonym or alias: not a place inside one
        options = names[phrase]
        here = [o for o in options if o[0] in named]
        if here:
            prefer = "region" if after in _REGION_WORDS else "city"
            best = max(here, key=lambda o: (o[4] == prefer, o[3]))
        else:
            best = max(options, key=lambda o: o[3])
            rivals = sorted((o[3] for o in options if o is not best), reverse=True)
            if best[3] < BIG_CITY or (rivals and rivals[0] * 5 > best[3]):
                continue
        key = (phrase, best[0])
        if key not in seen:
            seen.add(key)
            found.append({"name": phrase, "iso": best[0], "kind": best[4], "lat": best[1], "lon": best[2]})
    return found


@lru_cache(maxsize=1)
def _capitals() -> set[str]:
    return {n.strip() for v in CAPITALS.values() for n in v.split(",")} | {
        n.strip() for v in CITIES.values() for n in v.split(",")}


# ---------- local time and country facts ----------

def _dms(text: str, degrees: int) -> float:
    """"+4851" or "+0221" (±DDMM[SS] or ±DDDMM[SS]) as decimal degrees."""
    sign = -1 if text[0] == "-" else 1
    digits = text[1:]
    whole, rest = digits[:degrees], digits[degrees:]
    minutes, seconds = rest[:2], rest[2:4] or "0"
    return sign * (int(whole) + int(minutes) / 60 + int(seconds) / 3600)


@lru_cache(maxsize=1)
def _zones() -> dict[str, list[tuple[float, float, str]]]:
    """{country code: [(lat, lon, zone)]}, from the time zone database's zone1970.tab."""
    from importlib.resources import files
    zones: dict[str, list] = {}
    for line in files("tzdata.zoneinfo").joinpath("zone1970.tab").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        codes, coords, zone = line.split("\t")[:3]
        m = re.match(r"([+-]\d+)([+-]\d+)", coords)
        lat, lon = _dms(m.group(1), 2), _dms(m.group(2), 3)
        for code in codes.split(","):
            zones.setdefault(code, []).append((lat, lon, zone))
    return zones


# Capitals the nearest zone's main city gets wrong (Brasília is nearer Cuiabá than São Paulo).
CAPITAL_ZONES = {"BR": "America/Sao_Paulo", "KZ": "Asia/Almaty"}


def timezone(iso: str | None, lat: float | None = None, lon: float | None = None) -> str | None:
    """The time zone where something happens: the country's own, or in a country
    with several (the US, Russia, Brazil) the one whose main city is nearest."""
    options = _zones().get(iso or "", [])
    if not options:
        return None
    if len(options) == 1:
        return options[0][2]
    if lat is None or lon is None:
        if iso in CAPITAL_ZONES:
            return CAPITAL_ZONES[iso]
        lat, lon = _capital_at(iso)
    return min(options, key=lambda z: (z[0] - lat) ** 2 + ((z[1] - lon) * math.cos(math.radians(lat))) ** 2)[2]


def _capital_at(iso: str) -> tuple[float, float]:
    """Where a country's capital is (its middle when the gazetteer doesn't know it)."""
    for capital in (CAPITALS.get(iso) or "").split(","):
        for c_iso, lat, lon, _, kind in _gazetteer().get(capital.strip(), []):
            if c_iso == iso and kind == "city":
                return lat, lon
    c = _table()[0][iso]
    return c["lat"], c["lon"]


def country(iso: str) -> dict | None:
    """What a country card says: its name, capital, leader and time zone."""
    c = _table()[0].get(iso)
    if c is None:
        return None
    first = lambda table: (table.get(iso) or "").split(",")[0].strip() or None  # noqa: E731
    return {"iso": iso, "name": c["name"], "capital": first(CAPITALS), "leader": first(LEADERS),
            "zone": timezone(iso)}
