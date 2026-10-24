# Design and phases

How Situation decides what's on the page, how each decision is measured, what each phase did, and what's next. The product plan is in [`plan.md`](plan.md).

## The pipeline: four questions

Every article goes through four decisions, in order. Each has its own ground truth, its own metric, and its own method. A method can be a heuristic, hand-written rules, an off-the-shelf model or a language model, and each stage is chosen and improved on its own.

| Stage | Question | Objective or taste | Ground truth | Metric | Method today |
|---|---|---|---|---|---|
| **1. News** | Is this a report of something happening? | objective | Claude | precision and recall of what's dropped (dropping real news costs more than letting cruft through) | headline rule + `/opinion/` URLs (`score.is_news`) |
| **2. Event** | Do these articles report one thing that happened? | objective | Claude, weekly (`groundtruth.py`) | B-cubed, over all articles and headline-size events (`metrics.py`) | hand rules (`cluster.by_rules`) |
| **3. Situation** | Are these events part of one running story? | mixed: wars are objective, how wide is taste | Claude with your rubric, plus your checks | is each headline event under the right situation | Wikipedia's "Ongoing" list, matched by country, display only |
| **4. Tier** | Flash, priority or below, and in what order? | taste | you, on `/label` (rankings, "would you show it?") | ranking and bar agreement; is the top 10 right | hand-set score and thresholds (`score.assess`) |

On the side: **where** each event happens (`geo.locate`, or a place model trained on Claude's places).

Errors flow downstream: a non-news piece that gets through can join an event, a wrong merge inflates an event's outlet count, and that inflates its tier. So each stage is measured on its own, and the page is checked as a whole (is the top 10 right?).

## Principles

- **Define the unit before modelling.** Each stage's question, in one sentence, comes before any labels or models.
- **Objective questions get Claude's labels; taste gets yours.** Stages 1–3 are labelled by Claude in bulk; stage 4, and how wide a situation is, by you.
- **Evaluate on production data, raw.** Clean freely inside a model's pipeline, as long as production runs the same cleaning, and in its training data. Never clean the test set in a way the live app can't.
- **Simplest method first; escalate only on a measured need.** Rules, then off-the-shelf, then learned, then a language model.
- **Language models as labellers, rarely in production.** They're right for labelling high-volume objective stages. Live, they're only worth it where volume is low (stages 3–4), and the app must still work without one.
- **Curate datasets, don't just grow them.** Check label quality, report by stratum, and review each new batch before trusting it.
- **Every comparison comes with an interval,** against what's deployed, on the newest data.

## The apparatus

- **`metrics.py`**: B-cubed, headline-size, F1, bootstrap intervals. Pure functions.
- **`groundtruth.py`**: the event ground truth, one file per ISO week in `instance/groundtruth/` (not in git, backed up on the Pi). Each file records its rubric and model, its windows (a weekday and a weekend, news only) and Claude's events, plus a review of each window.
- **`tools/label_week.py`**: labels a week from a database copy through the Batch API (about $1–2). The database keeps 30 days, so any recent week can be labelled after the fact.
- **`tools/evaluate_grouping.py`**: compares event methods against the deployed one on the newest week, with intervals. A new method is one line in its table.
- **`labels.py` and `/label`**: your rankings and bar answers, and their scoring (`flask evaluate`, `flask train`).

## Phase 1: learning to group (October 2026)

**Aim.** Make grouping improve from labelled data instead of hand-tuned rules.

**Outcome**
- The non-news filter is in.
- Stage 2 has a weekly ground truth and an evaluation pipeline.
- The hand rules measure decently: 83% on headline-size events, 88% on all articles. The off-the-shelf grouping ties them, so they stay.
- Also from this phase: regions drawn as areas on the map, country cards, and local times.

**Parked** on the `learned-grouping` branch: two-stage learned grouping (events, then situations). It was vectorised to about 2 seconds a window.

**What we learned**
1. **We labelled situations before deciding the unit was the event,** so every comparison in that stretch measured the wrong thing.
2. **Holes in scoring flatter merging:** scoring only within labels made over-merging free (86% fell to 71% once fixed).
3. **Train on what you score:** pairs from inside one label alone made the models over-merge (precision went from 63% to 88% once fixed).
4. **Partition rules are a hypothesis.** Under the situation rules, five stories held a quarter of the articles, and sport and markets got pulled into wars.
5. **Off the shelf came close to learned,** at half the time.
6. **Headlines can't tell features from news.** About a quarter of articles aren't live news; the rule catches a fifth of those, and an embedding classifier did no better.
7. **Small, correlated data can't separate methods,** so gaps of a few points sit within noise.

## Roadmap

Ordered by what matters most now.

**Stage 4, tier** (what you see; taste)
- [ ] Rank and answer "would you show it?" on `/label` (40 rankings so far).
- [ ] Recalibrate the bar and the ten-story cap once the news filter is live: it changes outlet counts.
- [ ] Train the rank model when the rankings are enough; keep it only if it beats the hand score on held-out labels.

**Stage 1, news** (measure before fixing)
- [ ] Measure how much non-news reaches headline-size events. If little, stop here.
- [ ] If it matters: keep feeds' category tags and add per-outlet URL sections (cheap). Then, if needed, use the schema.org article type from the page.

**Stage 2, event** (good enough for now)
- [ ] Run `tools/label_week.py` weekly (or monthly for the last four weeks). Read each window's review.
- [ ] Label QA: Opus re-labels a sample of each week's bundles; check or drop the ones they disagree on.
- [ ] Report results by stratum (event size, region, topic), so we see where a method fails and whether the set covers enough of each.
- [ ] Study the hand rules' mistakes on headline-size events, and fix what's systematic.

**Stage 3, situation** (once events hold up)
- [ ] Situations as a layer over events (shown together, not merged), anchored on Wikipedia's "Ongoing" list.

**Later**
- A small historical "challenge set" for rare cases (results, ceasefires, leaders falling), if live weeks don't cover them.
- Revisit learned grouping (the branch) when labels span weeks.
- Speed only if needed (about 2 seconds a window on the Mac).
