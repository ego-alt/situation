"""Where a story happens: an offline lookup of countries (names, demonyms,
capitals and other newsworthy cities) in headlines and summaries.

The place mentioned most, weighted toward the headline, wins; ties go to the
first one mentioned. Places that are named mostly as actors ("Washington
says…", "Beijing warns…") count less than places where things happen."""
import json
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

# Short or common forms the country table doesn't carry (comma-separated).
ALIASES = {
    "US": "US, U.S., USA, America, Washington, Pentagon, White House",
    "GB": "UK, U.K., Britain, London, Downing Street",
    "RU": "Kremlin, Moscow",
    "CN": "Beijing",
    "KR": "Seoul",
    "KP": "Pyongyang",
    "TW": "Taipei",
    "PS": "Gaza, West Bank, Palestine, Hamas",
    "IL": "Netanyahu, Jerusalem, Tel Aviv",
    "UA": "Kyiv, Kiev, Zelensky, Zelenskyy",
    "FR": "Macron, Paris, Marseille, Lyon, Limoges",
    "DE": "Berlin",
    "IR": "Tehran",
    "YE": "Houthi, Houthis, Sanaa, Aden",
    "VA": "Vatican, Pope",
    "CD": "DRC, Kinshasa",
    "AE": "UAE, Dubai, Abu Dhabi",
    "SA": "Riyadh",
    "IN": "New Delhi, Delhi, Mumbai, Kolkata",
    "JP": "Tokyo",
    "TR": "Ankara, Istanbul",
    "EG": "Cairo",
    "PK": "Islamabad, Karachi, Lahore",
    "SY": "Damascus",
    "LB": "Beirut",
    "IQ": "Baghdad",
    "AF": "Kabul, Taliban",
    "SD": "Khartoum",
    "ET": "Addis Ababa",
    "KE": "Nairobi",
    "NG": "Lagos, Abuja",
    "BR": "Brasilia, Rio de Janeiro, São Paulo",
    "MX": "Mexico City",
    "VE": "Caracas",
    "AR": "Buenos Aires",
    "CA": "Ottawa, Toronto, Alberta, Carney",
    "AU": "Canberra, Sydney, Melbourne",
    "ID": "Jakarta",
    "PH": "Manila",
    "TH": "Bangkok",
    "VN": "Hanoi",
    "MM": "Myanmar",
    "BD": "Dhaka",
    "AL": "Tirana",
    "RS": "Belgrade",
    "PL": "Warsaw",
    "HU": "Budapest",
    "ES": "Madrid, Barcelona",
    "IT": "Rome, Milan",
    "GR": "Athens",
    "BE": "Brussels",
    "NL": "Amsterdam, The Hague",
}

# Places that aren't countries in the map's table but matter here.
EXTRA = {
    "HK": ("Hong Kong", 22.32, 114.17, "Hongkongers, Tai Po, Kowloon, Huanggang, John Lee"),
    "VA": ("Vatican", 41.90, 12.45, ""),
    "SG": ("Singapore", 1.35, 103.82, ""),
}

# Named as actors more than as places: count them less.
ACTOR_WORDS = {"Washington", "White House", "Pentagon", "Kremlin", "Beijing", "Moscow", "Brussels"}


@lru_cache(maxsize=1)
def _table():
    countries = {c["iso"]: c for c in json.loads(_DATA.read_text())}
    patterns = []
    split = lambda text: [n.strip() for n in (text or "").split(",") if n.strip()]  # noqa: E731
    for iso, c in countries.items():
        for n in {*c["names"], *split(DEMONYMS.get(iso)), *split(ALIASES.get(iso))}:
            if len(n) >= 2:
                patterns.append((n, iso))
    for iso, (name, lat, lon, more) in EXTRA.items():
        countries.setdefault(iso, {"iso": iso, "name": name, "lat": lat, "lon": lon})
        for n in [name, *split(more), *split(ALIASES.get(iso))]:
            patterns.append((n, iso))
    # Longest first, so "South Sudan" wins over "Sudan" and "Hong Kong" over "Kong".
    patterns.sort(key=lambda p: -len(p[0]))
    regex = re.compile(r"\b(" + "|".join(re.escape(p[0]) for p in patterns) + r")\b")
    lookup = {p[0]: p[1] for p in reversed(patterns)}
    return countries, regex, lookup


def places(text: str) -> Counter:
    countries, regex, lookup = _table()
    found = Counter()
    for m in regex.finditer(text or ""):
        iso = lookup.get(m.group(1))
        if iso:
            found[iso] += 0.4 if m.group(1) in ACTOR_WORDS else 1.0
    return found


def locate(articles: list[dict]) -> dict | None:
    """The main place of a group of articles: {"iso", "name", "lat", "lon", "x", "y"}."""
    total = Counter()
    for a in articles:
        total.update({k: v * 3 for k, v in places(a["title"]).items()})
        total.update(places(a.get("summary") or ""))
    if not total:
        return None
    iso, _ = total.most_common(1)[0]
    c = _table()[0][iso]
    x, y = project(c["lon"], c["lat"])
    return {"iso": iso, "name": c["name"], "lat": c["lat"], "lon": c["lon"], "x": x, "y": y}
