"""The server: the pages and their data, the Sync button, and the loop that fetches the news,
makes up Dispatch and asks the forecast question in the background."""
import os
import pathlib
import sys
import threading
import time
from datetime import timedelta

from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

import cli
import dispatch
import forecast
import labels
import page
import pipeline
import signals
from fetch import fetch_all, fetch_itn
from models import SourceStatus, db

STARTED = time.monotonic()
STALE = timedelta(hours=1)    # /healthz fails when no feed has answered for this long


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

    @app.route("/api/situation")
    def situation():
        """The page's data as last made up (page.py); a poll that already has it gets a 304."""
        response = app.response_class(page.published(), mimetype="application/json")
        response.add_etag()
        response.cache_control.no_cache = True      # kept by the browser, asked after every time
        return response.make_conditional(request)

    @app.route("/api/country/<iso>")
    def country_card(iso):
        card = page.country(iso.upper(), pipeline.clock())
        return jsonify(card) if card else ({"error": "no such country"}, 404)

    @app.route("/api/sync", methods=["POST"])
    def sync():
        """Fetch now instead of waiting for the next round. Joins a fetch that's
        already running; skips one if the last finished under SYNC_COOLDOWN ago."""
        return jsonify({"status": sync_now()})

    @app.route("/dispatch")
    @app.route("/dispatch/<edition>")
    def dispatch_page(edition=None):
        """Dispatch; /dispatch/2026-11-12 is that day's issue (the page reads it from the address)."""
        return render_template("dispatch.html")

    @app.route("/api/dispatch")
    def dispatch_api():
        """One issue (?edition=2026-11-12; the newest by default), with its question."""
        data = dispatch.issue(request.args.get("edition"))
        data["forecast"] = forecast.view((data["edition"] or {}).get("id"), _user(), pipeline.clock())
        return jsonify(data)

    @app.route("/api/forecast/<edition>", methods=["POST"])
    def forecast_answer(edition):
        """Your chance of Yes for an edition's question ({"chance": 0-100})."""
        now = pipeline.clock()
        try:
            forecast.answer(edition, _user(), int((request.get_json(silent=True) or {}).get("chance", -1)), now)
        except (ValueError, TypeError) as e:
            return {"error": str(e)}, 400
        except Exception:  # noqa: BLE001 — Polymarket didn't answer: nothing is saved
            return {"error": "Couldn’t reach Polymarket for the crowd’s odds. Try again in a minute."}, 502
        return jsonify(forecast.view(edition, _user(), now))

    def _user() -> str:
        """Whose guesses these are: the stack's signed-in user (trusted only when the stack says
        which header carries it), else the one local user."""
        header = os.environ.get("AUTH_PROXY_HEADER")
        return (request.headers.get(header) if header else None) or "local"

    @app.route("/label")
    def label_page():
        return render_template("label.html")

    @app.route("/api/label")
    def label_next():
        """A question to judge (see labels.py), and how many you've answered."""
        now = pipeline.clock()
        return jsonify({"question": labels.next_question(now), "counts": labels.counts(now)})

    @app.route("/api/label", methods=["POST"])
    def label_answer():
        try:
            labels.record(request.get_json(silent=True) or {})
        except ValueError as e:
            return {"error": str(e)}, 400
        return jsonify({"counts": labels.counts(pipeline.clock())})

    @app.route("/favicon.ico")
    def favicon():
        return send_from_directory(app.static_folder, "icon.svg", mimetype="image/svg+xml")

    @app.route("/healthz")
    def healthz():
        """Unhealthy once the app has run an hour without any feed answering:
        the page would be showing old news as if it were current."""
        last = pipeline.last_fetch_ok()
        at = last and last.isoformat()
        if time.monotonic() - STARTED > STALE.total_seconds() and (last is None or pipeline.clock() - last > STALE):
            return {"ok": False, "error": "no feed has answered for over an hour", "last_fetch": at}, 503
        return {"ok": True, "last_fetch": at}

    cli.register(app)
    if app.config["FETCH_LOOP"] and not app.config.get("TESTING") and _serving():
        start_loop(app)
    return app


def _serving() -> bool:
    """Whether this process is the web server (gunicorn, or `flask run` without
    the reloader's watcher process), not a one-off command like `flask fetch`:
    only the server runs the background fetch loop, or a command would fetch
    alongside it and both would store the same new articles."""
    if os.environ.get("WERKZEUG_RUN_MAIN") == "false":
        return False
    if os.environ.get("FLASK_RUN_FROM_CLI") != "true":
        return True   # a WSGI server imported the app
    return "run" in sys.argv[1:]


# One fetch at a time: the background loop and the Sync button share it.
_fetch_lock = threading.Lock()
SYNC_COOLDOWN = timedelta(minutes=2)


def sync_now() -> str:
    """"fetched", "joined" (one was already running; this waited for it) or
    "recent" (the last fetch was moments ago: spare the news sites)."""
    if not _fetch_lock.acquire(blocking=False):
        with _fetch_lock:
            return "joined"
    try:
        last = pipeline.last_fetch_ok()
        if last and pipeline.clock() - last < SYNC_COOLDOWN:
            return "recent"
        fetch_once()
        return "fetched"
    finally:
        _fetch_lock.release()


def fetch_once() -> dict:
    """One round of the news: read every feed and regroup (pipeline.run), look for disasters,
    forget what's old, and make the page up again."""
    now = pipeline.clock()
    report: dict = {}
    result = pipeline.run(lambda: fetch_all(report=report), fetch_itn, now)
    SourceStatus.record(report, now)
    try:
        signals.fetch(now)
    except Exception:  # noqa: BLE001 — the disaster services are extra: the news goes on without them
        db.session.rollback()
    pipeline.prune(now)
    labels.prune_snapshots(now)
    page.publish(now)
    return result


_loop_started = False


def start_loop(app):
    """Fetch every few minutes in the background (one thread per process), then make up
    Dispatch if it's due and ask the forecast question if it is."""
    global _loop_started
    if _loop_started:
        return
    _loop_started = True

    def loop():
        while True:
            try:
                with app.app_context(), _fetch_lock:
                    fetch_once()
            except Exception:  # noqa: BLE001 — a bad fetch must not stop the loop
                app.logger.exception("fetch failed")
            try:
                with app.app_context():
                    dispatch.publish(pipeline.clock())
            except Exception:  # noqa: BLE001 — nor a bad journal
                app.logger.exception("making up Dispatch failed")
            try:
                with app.app_context():
                    now = pipeline.clock()
                    if forecast.due(now):
                        forecast.run(now)
            except Exception:  # noqa: BLE001 — nor Polymarket
                app.logger.exception("forecast failed")
            time.sleep(app.config["FETCH_EVERY_MINUTES"] * 60)

    threading.Thread(target=loop, daemon=True, name="situation-fetch").start()
