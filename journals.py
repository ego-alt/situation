"""The journals Papers reads, and how each one's neuroscience is found.

Add a journal here to widen the paper. `scope` says which of its papers belong:
  "all"      every research paper (a neuroscience journal, like Neuron);
  "subject"  the ones the publisher files under neuroscience, read from its
             subject feed (`subject_feed`), as Nature does;
  "words"    the ones whose title or abstract talks about the nervous system
             (NEURO_WORDS), for a journal that files nothing by subject (Science).
`commentary` is what the journal calls the pieces it commissions on its own
papers (Nature files them with its news, so `commentary_page_type` picks them
out); `rank` breaks ties for the lead when no cover or commentary decides it.
"""
import re
from dataclasses import dataclass

SUBJECT = "Neuroscience"            # its section's heading in Dispatch


@dataclass(frozen=True)
class Journal:
    key: str
    name: str
    issn: str
    scope: str                      # all | subject | words
    commentary: str                 # News & Views, Perspective, Preview
    rank: int
    subject_feed: str = ""
    cover_page: str = ""            # the current issue's page, which names the cover story
    commentary_page_type: str = ""  # when commentary is filed with the news: the dc.type its page gives it


JOURNALS = [
    Journal("nature", "Nature", "0028-0836", "subject", "News & Views", 1,
            subject_feed="https://www.nature.com/subjects/neuroscience/nature.rss",
            cover_page="https://www.nature.com/nature/current-issue", commentary_page_type="News And Views"),
    Journal("science", "Science", "0036-8075", "words", "Perspective", 2),
    Journal("neuron", "Neuron", "0896-6273", "all", "Preview", 3),
]

BY_KEY = {j.key: j for j in JOURNALS}

# Words only the study of the nervous system uses. A paper is in when its title
# has one, or its abstract has two different ones ("neural network" doesn't count).
NEURO_WORDS = re.compile(
    r"\b(brains?|neur(?!al networks?)\w*|cortex|cortical|synap\w*|hippocamp\w*|cerebell\w*|thalam\w*|hypothalam\w*"
    r"|amygdala\w*|striat\w*|glia\w*|astrocyt\w*|microglia\w*|oligodendrocyt\w*|axon\w*|dendrit\w*|spinal cords?"
    r"|nervous systems?|alzheimer\w*|parkinson\w*|epilep\w*|schizophreni\w*|psychiatr\w*|optogenetic\w*"
    r"|electrophysiolog\w*|dopamin\w*|serotonin\w*)\b", re.I)
