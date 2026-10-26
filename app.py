import os
import pathlib
import sys
import threading
import time
from datetime import UTC, datetime, timedelta

import click
import numpy as np
from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from models import Story, db, migrate
from projection import project
from score import clean_title, is_headline
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

    @app.route("/api/country/<iso>")
    def country_card(iso):
        """A country's card: its capital, leader and time zone, and when it was last in the news here."""
        from geo import country
        from pipeline import clock
        card = country(iso.upper())
        if card is None:
            return {"error": "no such country"}, 404
        shown = Story.query.filter(Story.iso == card["iso"], Story.tier != "background")
        last = shown.order_by(Story.newest.desc()).first()
        month = (clock() - timedelta(days=30)).replace(tzinfo=None)
        return jsonify({**card, "stories_30d": shown.filter(Story.newest >= month).count(),
                        "last": {"headline": last.headline, "at": _iso(last.newest), "id": last.id} if last else None})

    @app.route("/api/sync", methods=["POST"])
    def sync():
        """Fetch now instead of waiting for the next round. Joins a fetch that's
        already running; skips one if the last finished under SYNC_COOLDOWN ago."""
        return jsonify({"status": sync_now()})

    @app.route("/label")
    def label_page():
        return render_template("label.html")

    @app.route("/api/label")
    def label_next():
        """A question to judge (see labels.py), and how many you've answered."""
        from labels import counts, next_question
        from pipeline import clock
        return jsonify({"question": next_question(clock()), "counts": counts(clock())})

    @app.route("/api/label", methods=["POST"])
    def label_answer():
        from labels import counts, record
        from pipeline import clock
        try:
            record(request.get_json(silent=True) or {})
        except ValueError as e:
            return {"error": str(e)}, 400
        return jsonify({"counts": counts(clock())})

    @app.cli.command("evaluate")
    @click.option("--set", "overrides", multiple=True, metavar="MODULE.NAME=VALUE",
                  help="Try a setting, e.g. score.ITN_BONUS=3 (repeatable).")
    @click.option("--holdout", default=None, type=int, help="Days of the newest labels to hold out.")
    @click.option("--hand", is_flag=True, help="Use the hand rules, ignoring any trained models.")
    def evaluate_command(overrides, holdout, hand):
        """Score the ranking and the bar against your labels from /label.
        (Grouping is scored against the ground truth: tools/evaluate_grouping.py.)"""
        import importlib
        from contextlib import nullcontext

        import learn
        from labels import HOLDOUT_DAYS, evaluate, split_holdout
        from models import Label
        for o in overrides:
            target, _, value = o.partition("=")
            module, _, name = target.rpartition(".")
            mod = importlib.import_module(module)
            if not hasattr(mod, name):
                raise click.BadParameter(f"{target} doesn't exist")
            setattr(mod, name, type(getattr(mod, name))(value))
        labels = Label.query.filter_by(source="you").all()
        older, held = split_holdout(labels, HOLDOUT_DAYS if holdout is None else holdout)
        with learn.using({}) if hand else nullcontext():
            for title, part in (("older", older), ("held out", held), ("all", labels)):
                print(_report(title, evaluate(part)))

    @app.cli.command("train")
    @click.option("--save", is_flag=True, help="Fit on everything and use the models from now on.")
    @click.option("--only", default="rank,place", help="Which models: rank (your labels), place (the ground truth).")
    @click.option("--groundtruth", default=None, help="The ground truth folder (default: groundtruth.directory()).")
    def train_command(save, only, groundtruth):
        """Fit the rank model on your rankings and the place model on the ground
        truth's events (learn.py), and say how they do on held-out data."""
        import learn
        from evaluation import groundtruth as truth
        from labels import HOLDOUT_DAYS, evaluate, place_examples, rank_examples, split_holdout
        from models import Label
        names = [n.strip() for n in only.split(",")]
        fitted = {}
        if "rank" in names:
            labels = Label.query.filter_by(source="you").all()
            older, held = split_holdout(labels, HOLDOUT_DAYS)
            model = learn.fit_rank(rank_examples(older))
            if model is None:
                print("rank: too few rankings to fit yet")
            else:
                with learn.using({}):
                    print(_report("hand", evaluate(held)))
                with learn.using({"rank": model}):
                    print(_report("learned", evaluate(held)))
                fitted["rank"] = learn.fit_rank(rank_examples(labels))
        if "place" in names:
            windows = truth.windows(groundtruth)
            if len(windows) < 2:
                print("place: needs two or more ground truth windows")
            else:
                model = learn.fit_place(place_examples(windows[:-1]))
                held = place_examples(windows[-1:])
                right = sum(int(np.argmax(x @ np.array(model["weights"]))) == t for x, t in held)
                hand = sum(int(np.argmax(x[:, learn.PLACE_FEATURES.index("share")])) == t for x, t in held)
                print(f"place on the newest window ({len(held)} events): hand rule {hand / len(held):.0%}, "
                      f"learned {right / len(held):.0%}")
                fitted["place"] = learn.fit_place(place_examples(windows))
        for name, model in fitted.items():
            print(f"{name}: {_describe(model)}")
            if save and model:
                stamp = datetime.now(UTC).isoformat(timespec="minutes")
                print(f"saved {learn.save(name, {**model, 'trained': stamp})}")

    def _describe(model):
        if "features" in model and isinstance(model["weights"], list):
            return ", ".join(f"{f} {w:+.2f}" for f, w in zip(model["features"], model["weights"], strict=True))
        return ", ".join(f"{k} {v:g}" for k, v in model["weights"].items())

    def _report(title, r):
        pct = lambda x: "—" if x is None else f"{x:.0%}"  # noqa: E731
        return (f"{title:9} rank pairs {r['rank_pairs']:4}  agreement {pct(r['rank_agreement']):>4}   "
                f"bar {r['bar_stories']:3}  agreement {pct(r['bar_agreement']):>4}")

    @app.cli.command("fetch")
    def fetch_command():
        """Fetch every source once and re-score."""
        with _fetch_lock:
            print(fetch_once())

    @app.cli.command("dropped")
    @click.option("--hours", default=24, help="How far back to look.")
    def dropped_command(hours):
        """List what the news model leaves out (news.py), surest first, to skim for mistakes."""
        import learn
        import news
        from models import Article
        model = learn.load("news")
        items = [a.as_item() for a in Article.query.filter(Article.fetched >= datetime.now(UTC).replace(tzinfo=None)
                                                            - timedelta(hours=hours))]
        p = news.chances(items, model) if model and items else None
        if p is None:
            print("no news model in use: the rules decide (news.is_news)")
            return
        out = sorted(((c, a) for a, c in zip(items, p, strict=True) if c >= model["threshold"]), key=lambda x: -x[0])
        print(f"{len(out)} of {len(items)} articles in the last {hours} hours left out "
              f"(threshold {model['threshold']:.2f})")
        for chance, a in out:
            print(f"  {chance:.2f}  {a['source']:11} {a['title'][:100]}")

    @app.cli.command("brief")
    def brief_command():
        """Print the morning brief: today's top headlines (pushing it comes later)."""
        print(brief_text(snapshot()))

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
    from labels import prune_snapshots
    prune_snapshots()
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
    # Newest first, one article per outlet (its latest).
    for a in sorted(s.articles, key=lambda a: a.published or a.fetched or datetime.min, reverse=True):
        if a.outlet in seen:
            continue
        seen.add(a.outlet)
        src = BY_KEY.get(a.source)
        # Updated only when it's meaningfully after it went out (feeds often stamp both alike).
        revised = a.updated if a.updated and a.published and (a.updated - a.published).total_seconds() > 600 else None
        sources.append({"outlet": src.name.split(" Hong Kong")[0] if src else a.outlet,
                        "title": clean_title(a.title), "url": a.url,
                        "published": _iso(a.published or a.fetched), "updated": _iso(revised)})
    spots = _story_spots(s)
    return {"id": s.id, "headline": s.headline, "tier": s.tier, "score": s.score, "outlets": s.outlets,
            "leads": s.leads, "itn": s.itn, "iso": s.iso, "place": s.place, "x": s.x, "y": s.y,
            "newest": _iso(s.newest), "first_seen": _iso(s.first_seen), "sources": sources,
            "spots": spots, "zone": _zone(s, spots), "latest": _latest(s, sources)}


def _zone(s: Story, spots: list[dict]) -> str | None:
    """The time zone where it's happening: its first place in its own country, else the country's."""
    from geo import timezone
    here = next((p for p in spots if p["iso"] == s.iso), None)
    return timezone(s.iso, here["lat"], here["lon"]) if here else timezone(s.iso)


LATEST = timedelta(hours=6)


def _develops(title, headline):
    """A title that moves the story on: it shares some of the headline's words (not
    another story chained in) but not most of them (not the headline reworded)."""
    from text import tokens
    a, b = set(tokens(title)), set(tokens(headline))
    return bool(a and b) and 0 < len(a & b) / min(len(a), len(b)) < 0.5


def _latest(s: Story, sources: list[dict]) -> dict | None:
    """The newest development, when it's recent and says something the headline
    doesn't: how a long-running story has moved on."""
    from pipeline import clock
    since = clock() - LATEST
    for src in sources:                       # newest first
        if not src["published"] or datetime.fromisoformat(src["published"]) < since:
            return None
        if is_headline(src["title"]) and _develops(src["title"], s.headline):
            return {"title": src["title"], "outlet": src["outlet"], "at": src["published"], "url": src["url"]}
    return None


def _spot(s: dict, **extra) -> dict:
    x, y = project(s["lon"], s["lat"])
    return {"name": s["name"], "iso": s["iso"], "kind": s["kind"], "x": x, "y": y,
            "lat": s["lat"], "lon": s["lon"], **extra}


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
