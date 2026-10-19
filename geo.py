"""Where a story happens: an offline lookup of countries (names, demonyms,
capitals, big cities and leaders) in headlines and summaries.

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
    for iso, c in countries.items():
        names = (DEMONYMS, ALIASES, CAPITALS, CITIES, LEADERS)
        for n in {*c["names"], *(x for table in names for x in split(table.get(iso)))}:
            if len(n) >= 2:
                patterns.append((n, iso))
    for iso, (name, lat, lon, more) in EXTRA.items():
        countries.setdefault(iso, {"iso": iso, "name": name, "lat": lat, "lon": lon})
        for n in [name, *split(more), *split(ALIASES.get(iso)), *split(LEADERS.get(iso))]:
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
            word = m.group(1)
            found[iso] += 0.4 if word in ACTOR_WORDS else 0.3 if word in AMBIGUOUS else 1.0
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
