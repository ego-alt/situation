"""The journals Dispatch reads, and which of each one's papers belong.

Add a journal here to widen the paper. `scope` says which of its papers belong:
  "all"      every research paper (a neuroscience journal, like Neuron);
  "subject"  the ones the publisher files under the subjects in `subject_feeds`,
             as Nature does;
  "words"    the ones whose title or abstract talks about one of FIELDS, for a
             journal that files nothing by subject (Science).
`commentary` is what the journal calls the pieces it commissions on its own
papers (Nature files them with its news, so `commentary_page_type` picks them
out); `rank` orders a day's papers.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Journal:
    key: str
    name: str
    issn: str
    scope: str                      # all | subject | words
    commentary: str                 # News & Views, Perspective, Preview
    rank: int
    subject_feeds: tuple = ()
    cover_page: str = ""            # the current issue's page, which names the cover story
    commentary_page_type: str = ""  # when commentary is filed with the news: the dc.type its page gives it


# Nature's names for what FIELDS covers.
SUBJECTS = ("neuroscience", "psychology", "human-behaviour", "social-sciences", "zoology", "animal-behaviour",
            "evolution", "palaeontology", "climate-sciences")

JOURNALS = [
    Journal("nature", "Nature", "0028-0836", "subject", "News & Views", 1,
            subject_feeds=tuple(f"https://www.nature.com/subjects/{s}/nature.rss" for s in SUBJECTS),
            cover_page="https://www.nature.com/nature/current-issue", commentary_page_type="News And Views"),
    Journal("science", "Science", "0036-8075", "words", "Perspective", 2),
    Journal("neuron", "Neuron", "0896-6273", "all", "Preview", 3),
    Journal("pnas", "PNAS", "0027-8424", "words", "Commentary", 4),
    Journal("plosbiology", "PLOS Biology", "1544-9173", "words", "Primer", 5),
]

BY_KEY = {j.key: j for j in JOURNALS}


def _words(*words: str):
    return re.compile(r"\b(" + "|".join(words) + r")\b", re.I)


# What the paper is about: the brain, animals, people and the Earth, each by words only its
# study uses. A paper is in when its title has one, or its abstract has two different ones of
# one field ("neural network" doesn't count, nor a "random forest").
FIELDS = {
    "brain": _words(
        r"brains?", r"neur(?!al networks?)\w*", "cortex", "cortical", r"synap\w*", r"hippocamp\w*", r"cerebell\w*",
        r"thalam\w*", r"hypothalam\w*", r"amygdala\w*", r"striat\w*", r"glia\w*", r"astrocyt\w*", r"microglia\w*",
        r"oligodendrocyt\w*", r"axon\w*", r"dendrit\w*", "spinal cords?", "nervous systems?", r"alzheimer\w*",
        r"parkinson\w*", r"epilep\w*", r"schizophreni\w*", r"psychiatr\w*", r"optogenetic\w*",
        r"electrophysiolog\w*", r"dopamin\w*", r"serotonin\w*"),
    "animals": _words(
        "animals?", r"zoolog\w*", r"mammal\w*", "birds?", "avian", "songbirds?", "insects?", "beetles?",
        r"butterfl\w*", "moths?", "ants?", "bees?", "wasps?", "spiders?", r"fish\w*", "sharks?", "frogs?",
        r"amphibian\w*", r"reptil\w*", "lizards?", "snakes?", "turtles?", r"crocodil\w*", "primates?", "monkeys?",
        "apes?", r"chimpanzee\w*", "gorillas?", "whales?", "dolphins?", "seals?", r"marsupial\w*", r"vertebrate\w*",
        r"invertebrate\w*", r"coral\w*", r"octop\w*", "squid", r"cephalopod\w*", r"crustacean\w*", "crabs?",
        "snails?", r"mollus\w*", r"dinosaur\w*", r"fossil\w*", r"pterosaur\w*", "predators?", "prey", "foraging",
        "mating", "courtship", r"herbivor\w*", r"carnivor\w*", r"venom\w*", "camouflage", r"echolocat\w*",
        "wildlife", "wolves", "wolf", "lions?", "elephants?", "penguins?", "parrots?", "crows?", r"corvid\w*"),
    "people": _words(
        r"psycholog\w*", r"societ\w*", r"linguist\w*", "languages?", r"econom\w*", r"inequalit\w*", r"cooperat\w*",
        r"politic\w*", r"democra\w*", "voters?", "elections?", r"homin\w*", r"neanderthal\w*", r"archaeolog\w*",
        "marriage", "kinship", r"educat\w*", "poverty", r"wealth\w*", "cities", r"urban\w*", "childhood",
        r"beliefs?", r"moral\w*", r"religio\w*", "incomes?", "institutions?", "governments?", "misinformation",
        "happiness", "well-being", r"lonel\w*"),
    "earth": _words(
        r"climat\w*", "oceans?", r"glaci\w*", "ice ages?", "ice sheets?", r"volcan\w*", r"earthquak\w*", "rivers?",
        r"(?<!random )forests?", "monsoons?", "sea levels?", r"atmospher\w*", r"geolog\w*", r"tecton\w*",
        "droughts?", "floods?", "wildfires?", "deserts?", "arctic", r"antarctic\w*", "rainfall", "carbon cycle",
        "biodiversity", "ecosystems?", r"extinct\w*"),
}
