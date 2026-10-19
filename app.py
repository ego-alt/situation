import os
import pathlib
import threading
import time
from datetime import UTC, datetime, timedelta

import click
from flask import Flask, jsonify, render_template, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from models import Story, db, utcnow
from score import clean_title
from sources import BY_KEY


def create_app(config=None):
    app = Flask(__name__)
    app.url_map.strict_slashes = False
    instance = pathlib.Path(app.instance_path)
    instance.mkdir(parents=True, exist_ok=True)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{instance / 'situation.db'}",
        FETCH_EVERY_MINUTES=int(os.environ.get("FETCH_EVERY_MINUTES", "15")),
        FETCH_LOOP=os.environ.get("FETCH_LOOP", "1") == "1",
    )
    if config:
        app.config.update(config)
    db.init_app(app)
    with app.app_context():
        db.create_all()
    # Behind the dashboard's nginx at /situation/: trust its X-Forwarded-*
    # headers, so url_for() and request.script_root include the prefix.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(app.static_folder, "icon.svg", mimetype="image/svg+xml")

    @app.route("/healthz")
    def healthz():
        return {"ok": True}

    @app.route("/api/situation")
    def situation():
        return jsonify(snapshot())

    @app.cli.command("fetch")
    def fetch_command():
        """Fetch every source once and re-score."""
        print(fetch_once())

    @app.cli.command("brief")
    @click.option("--dry-run", is_flag=True, default=True, help="Print the brief (pushing comes later).")
    def brief_command(dry_run):
        """The morning brief: today's top headlines."""
        print(brief_text(snapshot()))

    if app.config["FETCH_LOOP"] and not app.config.get("TESTING") and os.environ.get("WERKZEUG_RUN_MAIN") != "false":
        start_loop(app)
    return app


def fetch_once(now=None) -> dict:
    from fetch import fetch_all, fetch_itn
    from pipeline import prune, run
    result = run(fetch_all, fetch_itn, now)
    prune()
    return result


_loop_started = False


def start_loop(app):
    """Fetch every few minutes in the background (one thread per process)."""
    global _loop_started
    if _loop_started:
        return
    _loop_started = True

    def loop():
        while True:
            try:
                with app.app_context():
                    fetch_once()
            except Exception:  # noqa: BLE001 — a bad fetch must not stop the loop
                app.logger.exception("fetch failed")
            time.sleep(app.config["FETCH_EVERY_MINUTES"] * 60)

    threading.Thread(target=loop, daemon=True, name="situation-fetch").start()


def _iso(dt):
    return dt.replace(tzinfo=UTC).isoformat() if dt else None


def _story(s: Story) -> dict:
    seen = set()
    sources = []
    for a in sorted(s.articles, key=lambda a: (a.position if a.position is not None else 99)):
        if a.outlet in seen:
            continue
        seen.add(a.outlet)
        src = BY_KEY.get(a.source)
        sources.append({"outlet": src.name.split(" Hong Kong")[0] if src else a.outlet,
                        "title": clean_title(a.title), "url": a.url})
    return {"id": s.id, "headline": s.headline, "tier": s.tier, "score": s.score, "outlets": s.outlets,
            "leads": s.leads, "itn": s.itn, "iso": s.iso, "place": s.place, "x": s.x, "y": s.y,
            "newest": _iso(s.newest), "first_seen": _iso(s.first_seen), "sources": sources}


def snapshot() -> dict:
    from pipeline import latest_itn, ongoing_situations
    day_ago = utcnow() - timedelta(hours=24)
    stories = Story.query.filter(Story.tier != "background").order_by(Story.score.desc()).all()
    recent = [s for s in stories if (s.newest or s.first_seen) >= day_ago]
    earlier = [s for s in stories if s not in recent]
    itn = latest_itn()
    everything = Story.query.all()
    last = db.session.query(db.func.max(Story.newest)).scalar()
    from models import Article
    fetched = db.session.query(db.func.max(Article.fetched)).scalar()
    return {
        "now": datetime.now(UTC).isoformat(),
        "fetched": _iso(fetched),
        "latest": _iso(last),
        "stories": [_story(s) for s in recent],
        "earlier": [_story(s) for s in earlier[:10]],
        "ongoing": [{"topic": o["topic"], "isos": o["isos"], "story_ids": [s.id for s in o["stories"]],
                     "latest": o["stories"][0].headline if o["stories"] else None}
                    for o in ongoing_situations(everything, itn)],
        "itn": itn.get("items", []),
        "counts": {"major": sum(s.tier == "major" for s in recent),
                   "headline": sum(s.tier == "headline" for s in recent),
                   "watched": len(everything)},
    }


def brief_text(snap: dict, limit: int = 5) -> str:
    top = snap["stories"][:limit]
    if not top:
        return "Quiet day: nothing at headline level in the last 24 hours."
    return "\n".join(f"{'▲' if s['tier'] == 'major' else '·'} {s['headline']}" for s in top)
