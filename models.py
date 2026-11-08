from datetime import UTC, datetime

from flask_sqlalchemy import SQLAlchemy

from score import BACKGROUND

db = SQLAlchemy()


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)   # stored naive, always UTC


story_articles = db.Table(
    "story_articles",
    db.Column("story_id", db.Integer, db.ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True),
    db.Column("article_id", db.Integer, db.ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True),
)


class Article(db.Model):
    """One item from one feed."""
    __tablename__ = "articles"
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String, unique=True, nullable=False)
    source = db.Column(db.String, nullable=False, index=True)
    outlet = db.Column(db.String, nullable=False)
    title = db.Column(db.String, nullable=False)
    summary = db.Column(db.String)
    published = db.Column(db.DateTime, index=True)      # UTC
    updated = db.Column(db.DateTime)                    # UTC, when the outlet last revised it (if it says)
    position = db.Column(db.Integer)                    # place in its feed when last seen (0 = lead)
    fetched = db.Column(db.DateTime, default=utcnow, index=True)
    spots = db.Column(db.JSON)                          # cities and regions it names (geo.spots)

    def as_item(self) -> dict:
        return {"id": self.id, "source": self.source, "outlet": self.outlet, "title": self.title,
                "summary": self.summary, "url": self.url, "position": self.position,
                "published": (self.published or self.fetched).replace(tzinfo=UTC)}


class Story(db.Model):
    """A group of articles about one event, re-grouped on every fetch; its id
    carries over while most of its articles do."""
    __tablename__ = "stories"
    id = db.Column(db.Integer, primary_key=True)
    headline = db.Column(db.String, nullable=False)
    tier = db.Column(db.String, nullable=False, index=True)     # major | headline | background
    score = db.Column(db.Float, nullable=False, default=0)
    outlets = db.Column(db.Integer, nullable=False, default=0)
    leads = db.Column(db.Integer, nullable=False, default=0)
    itn = db.Column(db.String)                                  # the "In the news" item it matched
    iso = db.Column(db.String)                                  # where: country code
    place = db.Column(db.String)
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    x = db.Column(db.Float)                                     # map position (projection.py)
    y = db.Column(db.Float)
    first_seen = db.Column(db.DateTime, default=utcnow)
    newest = db.Column(db.DateTime)
    # Closed once none of its articles are in the regrouping window any more;
    # kept (with its id) so nothing is pushed twice.
    closed = db.Column(db.Boolean, nullable=False, default=False, index=True)
    # Ever tiered above background: it was on the page, so when it leaves the
    # window it's closed, not deleted, even if it had faded by then.
    shown = db.Column(db.Boolean, nullable=False, default=False)
    # A running story (situations.py) is a story too: all its events' articles, its
    # own tier and place, and its name as the headline. Its events point to it.
    running = db.Column(db.Boolean, nullable=False, default=False, index=True)
    run_id = db.Column(db.Integer, index=True)                  # an event: the running story it's part of
    articles = db.relationship("Article", secondary=story_articles, lazy="selectin")

    def rank(self, headline: str, tier: str, score: float):
        """Its headline, tier and score from a regrouping (the only place a tier is set)."""
        self.headline, self.tier, self.score = headline, tier, score
        self.shown = self.shown or tier != BACKGROUND


class ItnSnapshot(db.Model):
    """What Wikipedia's "In the news" said at a fetch."""
    __tablename__ = "itn_snapshots"
    id = db.Column(db.Integer, primary_key=True)
    fetched = db.Column(db.DateTime, default=utcnow, index=True)
    items = db.Column(db.JSON, nullable=False)
    ongoing = db.Column(db.JSON, nullable=False)


class SourceStatus(db.Model):
    """How each feed did on its last fetches, so a broken one is noticed."""
    __tablename__ = "source_status"
    key = db.Column(db.String, primary_key=True)
    last_try = db.Column(db.DateTime)
    last_ok = db.Column(db.DateTime)            # last fetch that returned items
    items = db.Column(db.Integer, nullable=False, default=0)
    error = db.Column(db.String)


class Signal(db.Model):
    """What a monitoring service saw (signals.py): a disaster alert or a strong earthquake,
    often before the news does. Kept while it's happening and for a while after."""
    __tablename__ = "signals"
    id = db.Column(db.String, primary_key=True)                 # "gdacs:TC:1001346", "usgs:us7000abcd"
    source = db.Column(db.String, nullable=False)               # GDACS | USGS
    kind = db.Column(db.String, nullable=False)                 # EQ earthquake, TC cyclone, VO eruption, FL flood
    level = db.Column(db.String, nullable=False)                # orange | red (USGS: from its PAGER alert, else orange)
    title = db.Column(db.String, nullable=False)                # "M7.0 earthquake", "Cyclone Murasu"
    place = db.Column(db.String)                                # "China", "43 km ESE of Port-Olry, Vanuatu"
    iso = db.Column(db.String, index=True)
    lat = db.Column(db.Float, nullable=False)
    lon = db.Column(db.Float, nullable=False)
    magnitude = db.Column(db.Float)
    started = db.Column(db.DateTime, nullable=False, index=True)
    ended = db.Column(db.DateTime, nullable=False)              # an earthquake ends when it starts
    url = db.Column(db.String)
    seen = db.Column(db.DateTime, default=utcnow)               # when Situation first saw it


class DayTally(db.Model):
    """The most stories shown at once on each day (UTC), to tune the bar."""
    __tablename__ = "day_tallies"
    day = db.Column(db.Date, primary_key=True)
    major = db.Column(db.Integer, nullable=False, default=0)
    headline = db.Column(db.Integer, nullable=False, default=0)
    cleared = db.Column(db.Integer, nullable=False, default=0)    # before the cap


class Label(db.Model):
    """One of your judgements on /label (see labels.py)."""
    __tablename__ = "labels"
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String, nullable=False, index=True)     # rank | show
    snapshot = db.Column(db.String, nullable=False, index=True) # instance/snapshots/<name>.json.gz
    a = db.Column(db.JSON, nullable=False)                      # same: an article; rank: a story's headline and urls
    b = db.Column(db.JSON, nullable=False)
    answer = db.Column(db.String, nullable=False)               # a|b|equal, or yes|no|borderline
    at = db.Column(db.DateTime, default=utcnow, index=True)
    source = db.Column(db.String, nullable=False, default="you", index=True)   # you, or claude:<model>


class Paper(db.Model):
    """A research paper in one of the journals Papers reads (journals.py), or a
    commentary the journal commissioned on one (`on` is that paper's DOI)."""
    __tablename__ = "papers"
    doi = db.Column(db.String, primary_key=True)                # lower case
    journal = db.Column(db.String, nullable=False, index=True)  # journals.py key
    kind = db.Column(db.String, nullable=False, index=True)     # research | commentary | other (looked at, not either)
    title = db.Column(db.String, nullable=False)                # plain text, with <i>, <sub>, <sup> kept
    authors = db.Column(db.JSON)                                # surnames, in order
    abstract = db.Column(db.String)
    published = db.Column(db.Date, index=True)                  # first out, online or in print: its edition's week
    url = db.Column(db.String)
    figure = db.Column(db.String)                               # an image of its first figure or graphical abstract
    free = db.Column(db.JSON)                                   # {"url", "label"}: a copy anyone can read
    on = db.Column(db.String, index=True)                       # commentary: the DOI it's about
    cover = db.Column(db.String)                                # research: the cover image, when it's the cover story
    fetched = db.Column(db.DateTime, default=utcnow)
    enriched = db.Column(db.DateTime)                           # when its figure was looked for


def migrate():
    """Add columns that create_all() can't add to an existing table."""
    def columns(table):
        return {row[1] for row in db.session.execute(db.text(f"PRAGMA table_info({table})"))}
    stories = columns("stories")
    if stories and "shown" not in stories:
        db.session.execute(db.text("ALTER TABLE stories ADD COLUMN shown BOOLEAN NOT NULL DEFAULT 0"))
        # Its tier now is all there is to go on for a story from before.
        db.session.execute(db.text("UPDATE stories SET shown = 1 WHERE tier != 'background'"))
    db.session.commit()
