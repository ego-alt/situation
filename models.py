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
    position = db.Column(db.Integer)                    # place in its feed when last seen (0 = lead)
    fetched = db.Column(db.DateTime, default=utcnow, index=True)

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
    articles = db.relationship("Article", secondary=story_articles, lazy="selectin")


class ItnSnapshot(db.Model):
    """What Wikipedia's "In the news" said at a fetch."""
    __tablename__ = "itn_snapshots"
    id = db.Column(db.Integer, primary_key=True)
    fetched = db.Column(db.DateTime, default=utcnow, index=True)
    items = db.Column(db.JSON, nullable=False)
    ongoing = db.Column(db.JSON, nullable=False)
