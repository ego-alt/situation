from datetime import UTC, datetime

from flask_sqlalchemy import SQLAlchemy

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
    # kept (with its id) for "Earlier this week" and so nothing is pushed twice.
    closed = db.Column(db.Boolean, nullable=False, default=False, index=True)
    articles = db.relationship("Article", secondary=story_articles, lazy="selectin")


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
    kind = db.Column(db.String, nullable=False, index=True)     # same | rank
    snapshot = db.Column(db.String, nullable=False, index=True) # instance/snapshots/<name>.json.gz
    a = db.Column(db.JSON, nullable=False)                      # same: an article; rank: a story's headline and urls
    b = db.Column(db.JSON, nullable=False)
    answer = db.Column(db.String, nullable=False)               # same|different|unsure, or a|b|equal
    at = db.Column(db.DateTime, default=utcnow, index=True)


def migrate():
    """Add columns that create_all() can't add to an existing table."""
    def columns(table):
        return {row[1] for row in db.session.execute(db.text(f"PRAGMA table_info({table})"))}
    stories, articles = columns("stories"), columns("articles")
    if stories and "closed" not in stories:
        db.session.execute(db.text("ALTER TABLE stories ADD COLUMN closed BOOLEAN NOT NULL DEFAULT 0"))
        db.session.execute(db.text("CREATE INDEX IF NOT EXISTS ix_stories_closed ON stories (closed)"))
    if articles and "spots" not in articles:
        db.session.execute(db.text("ALTER TABLE articles ADD COLUMN spots JSON"))   # filled in by the next fetch
    if articles and "updated" not in articles:
        db.session.execute(db.text("ALTER TABLE articles ADD COLUMN updated DATETIME"))
    db.session.commit()
