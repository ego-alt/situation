import os
import pathlib
import threading
import time
from datetime import UTC, timedelta

from flask import Flask, jsonify, render_template, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from models import Story, db, migrate
from projection import project
from score import clean_title
from sources import BY_KEY, SOURCES

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
        migrate()
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
        """Unhealthy once the app has run an hour without any feed answering:
        the page would be showing old news as if it were current."""
        from pipeline import clock, last_fetch_ok
        last = last_fetch_ok()
        if time.monotonic() - STARTED > STALE.total_seconds() and (last is None or clock() - last > STALE):
            return {"ok": False, "error": "no feed has answered for over an hour",
                    "last_fetch": _iso(last)}, 503
        return {"ok": True, "last_fetch": _iso(last)}

    @app.route("/api/situation")
    def situation():
        return jsonify(snapshot())

    @app.route("/api/sync", methods=["POST"])
    def sync():
        """Fetch now instead of waiting for the next round. Joins a fetch that's
        already running; skips one if the last finished under SYNC_COOLDOWN ago."""
        return jsonify({"status": sync_now()})

    @app.cli.command("fetch")
    def fetch_command():
        """Fetch every source once and re-score."""
        print(fetch_once())

    @app.cli.command("brief")
    def brief_command():
        """Print the morning brief: today's top headlines (pushing it comes later)."""
        print(brief_text(snapshot()))

    if app.config["FETCH_LOOP"] and not app.config.get("TESTING") and os.environ.get("WERKZEUG_RUN_MAIN") != "false":
        start_loop(app)
    return app


# One fetch at a time: the background loop and the Sync button share it.
_fetch_lock = threading.Lock()
SYNC_COOLDOWN = timedelta(minutes=2)


def sync_now() -> str:
    """"fetched", "joined" (one was already running; this waited for it) or
    "recent" (the last fetch was moments ago: spare the news sites)."""
    from pipeline import clock, last_fetch_ok
    if not _fetch_lock.acquire(blocking=False):
        with _fetch_lock:
            return "joined"
    try:
        last = last_fetch_ok()
        if last and clock() - last < SYNC_COOLDOWN:
            return "recent"
        fetch_once()
        return "fetched"
    finally:
        _fetch_lock.release()


def fetch_once(now=None) -> dict:
    from fetch import fetch_all, fetch_itn
    from pipeline import prune, record_sources, run
    report: dict = {}
    result = run(lambda: fetch_all(report=report), fetch_itn, now)
    record_sources(report, now)
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
                with app.app_context(), _fetch_lock:
                    fetch_once()
            except Exception:  # noqa: BLE001 — a bad fetch must not stop the loop
                app.logger.exception("fetch failed")
            time.sleep(app.config["FETCH_EVERY_MINUTES"] * 60)

    threading.Thread(target=loop, daemon=True, name="situation-fetch").start()


def _iso(dt):
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat() if dt else None


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
            "newest": _iso(s.newest), "first_seen": _iso(s.first_seen), "sources": sources,
            "spots": _story_spots(s)}


def _spot(s: dict, **extra) -> dict:
    x, y = project(s["lon"], s["lat"])
    return {"name": s["name"], "iso": s["iso"], "kind": s["kind"], "x": x, "y": y, **extra}


def _story_spots(s: Story, limit: int = 8) -> list[dict]:
    """The cities and regions a story's articles name, most named first: those in
    its own country, and elsewhere only what two or more of its articles name
    (one article's aside about another country isn't where the story is)."""
    counts: dict[tuple, list] = {}
    for a in s.articles:
        for spot in a.spots or []:
            counts.setdefault((spot["name"], spot["iso"]), [spot, 0])[1] += 1
    counts = {k: c for k, c in counts.items() if k[1] == s.iso or c[1] >= 2}
    ranked = sorted(counts.values(), key=lambda c: -c[1])[:limit]
    return [_spot(spot, n=n) for spot, n in ranked]


EARLIER = timedelta(days=7)


def snapshot() -> dict:
    from pipeline import SHOWN, clock, last_fetch_ok, latest_itn, on_screen, ongoing_situations, source_health, tracks
    now = clock()
    shown, cleared = on_screen(now)
    day_ago, week_ago = (now - SHOWN).replace(tzinfo=None), (now - EARLIER).replace(tzinfo=None)
    # The week before today, newest first: stories that cleared the bar, open or closed.
    earlier = (Story.query.filter(Story.tier != "background", Story.newest < day_ago, Story.newest >= week_ago)
               .order_by(Story.newest.desc()).limit(15).all())
    itn = latest_itn()
    current = Story.query.filter_by(closed=False).all()
    ongoing = ongoing_situations(current, itn, now)
    weeks = tracks([set(o["isos"]) for o in ongoing], now)
    return {
        "now": now.isoformat(),
        "fetched": _iso(last_fetch_ok()),
        "stories": [_story(s) for s in shown],
        "cleared": cleared,
        "earlier": [_story(s) for s in earlier],
        "ongoing": [{"topic": o["topic"], "isos": o["isos"], "story_ids": [s.id for s in o["stories"]],
                     "latest": o["latest"].headline if o["latest"] else None, "singles": o["singles"],
                     "track": [_spot(t, n=t["n"], last=_iso(t["last"])) for t in week]}
                    for o, week in zip(ongoing, weeks, strict=True)],
        "itn": itn.get("items", []),
        "sources": source_health(SOURCES, now),
        "counts": {"major": sum(s.tier == "major" for s in shown),
                   "headline": sum(s.tier == "headline" for s in shown),
                   "watched": len(current)},
    }


def brief_text(snap: dict, limit: int = 5) -> str:
    top = snap["stories"][:limit]
    if not top:
        return "Quiet day: nothing at headline level in the last 24 hours."
    return "\n".join(f"{'▲' if s['tier'] == 'major' else '·'} {s['headline']}" for s in top)
