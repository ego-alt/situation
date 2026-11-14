"""The commands (`uv run flask --app app:create_app <name>`): the server's jobs done now (fetch,
dispatch, forecast, brief), and the tuning of the ranking against your labels (evaluate, train,
dropped)."""
import importlib
from contextlib import nullcontext
from datetime import timedelta

import click
import numpy as np

import dispatch
import forecast
import learn
import news
import page
import pipeline
from evaluation import groundtruth as truth
from labels import HOLDOUT_DAYS, evaluate, place_examples, rank_examples, split_holdout
from models import Article, Label, Question


def register(app):
    @app.cli.command("evaluate")
    @click.option("--set", "overrides", multiple=True, metavar="MODULE.NAME=VALUE",
                  help="Try a setting, e.g. score.ITN_BONUS=3 (repeatable).")
    @click.option("--holdout", default=None, type=int, help="Days of the newest labels to hold out.")
    @click.option("--hand", is_flag=True, help="Use the hand rules, ignoring any trained models.")
    def evaluate_command(overrides, holdout, hand):
        """Score the ranking and the bar against your labels from /label.
        (Grouping is scored against the ground truth: tools/evaluate_grouping.py.)"""
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
                stamp = pipeline.clock().isoformat(timespec="minutes")
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
        from app import fetch_once  # here, not above: app.py is what registers these commands
        print(fetch_once())

    @app.cli.command("dispatch")
    def dispatch_command():
        """Make up today's issue of Dispatch now, if it isn't yet (the server does as it comes out)."""
        print("published:", dispatch.publish(pipeline.clock()))

    @app.cli.command("forecast")
    def forecast_command():
        """Ask today's forecast question if it hasn't been, and look for results (the server does hourly)."""
        forecast.run(pipeline.clock())
        for q in Question.query.order_by(Question.edition.desc()).limit(4):
            print(q.edition, q.state, q.title or "(nothing eligible)")

    @app.cli.command("dropped")
    @click.option("--hours", default=24, help="How far back to look.")
    def dropped_command(hours):
        """List what the news model leaves out (news.py), surest first, to skim for mistakes."""
        model = learn.load("news")
        since = pipeline.clock() - timedelta(hours=hours)
        items = [a.as_item() for a in Article.query.filter(Article.fetched >= since)]
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
        print(page.brief(page.build(pipeline.clock())))
