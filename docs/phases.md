# Phases

A retrospective per phase, and the road ahead. The product plan is in [`plan.md`](plan.md).

## Phase 1: learning to group (October 2026)

**Aim.** Make grouping (which articles are one story) and placing improve from labelled data instead of hand-tuned rules.

**Kept**

- **`/label`**: rankings ("which matters more?"), the bar ("would you want this on your page today?"), and checks of Claude's stories. Every label records who made it.
- **Claude labelling** (`tools/label_week.py` today): snapshots cut from the database, split into bundles of loosely related articles, each labelled through the Batch API. About $2 a run.
- **Scoring**: B-cubed (per article, so one huge story can't dominate) over whole snapshots, a headline-size score (stories three or more outlets carry, the ones the page can show), and bootstrap intervals (`metrics.py`, `tools/evaluate_grouping.py` today).
- **A non-news filter** (`score.is_news`): opinion, explainers, look-backs and non-articles are left out of grouping.
- **An off-the-shelf grouping**, average linkage on meaning vectors, switched on by `instance/models/meaning.json`. Not on yet.
- **Map**: regions drawn as areas, country cards, local times on stories.

**Parked** on the `learned-grouping` branch: two-stage learned grouping. Stage 1 put articles into events (a logistic regression on nine pair signals plus neighbour features, then correlation clustering). Stage 2 joined events into situations (a story-level model using shared names, countries and Wikipedia's "Ongoing" list). It was vectorised to about 2 seconds a window.

**What we learned**

1. **Define the unit before modelling.** After the grouping interview, the labels became situation-level ("the Ukraine war is one story"). Only later did we settle that the dashboard's unit is the *event*. Every comparison in between measured the wrong thing.
2. **Holes in scoring flatter merging.** Scoring only articles within one label made over-merging free. Once whole snapshots were scored, the learned model fell from 86% to 71%.
3. **Train on what you score.** Training on pairs inside one label left out the commonest "different", and the models over-merged. Training on every candidate pair lifted precision from 63% to 88%.
4. **Partition rules are a hypothesis; check the data they produce.** Under the situation rules:
   - the five biggest stories held about a quarter of all articles;
   - sport and market reports got pulled into wars and protests;
   - a quarter of articles were never labelled;
   - most stories were single-outlet items the page never shows.
5. **Try off the shelf first.** Average linkage on embeddings came within a few points of the learned model at half the time. HDBSCAN did worse, because small stories become noise.
6. **Headlines can't tell features from news.** About a quarter of articles aren't live news. The headline rule catches a fifth of those, and an embedding classifier did no better.
7. **The data was too small to separate methods.** It covered 8 days, with 3 test windows and the same big situations running through all of them, so gaps of a few points sit within noise.

## Roadmap

**Phase 2: events, measured properly**

- [x] Event-level ground truth (October 2026, week 43: 3 windows, 4,241 news articles, 218 headline-size events; healthy partition, the 5 biggest events about 7% of articles). The hand rules against the off-the-shelf grouping, each window held out in turn and the off-the-shelf cut tuned on headline-size F1: a tie, 83% each on headline-size events (−0.2%, interval −3.5 to +2.8) and 88% each on all articles. The hand rules find more of each event, the off-the-shelf method is more precise. No switch. (The earlier "win" for the off-the-shelf method came from situation-level labels, which reward merging more.)
- [x] The apparatus, cleaned up:
  - `metrics.py`: B-cubed, headline-size, bootstrap intervals; pure functions.
  - `groundtruth.py`: one file per week in `instance/groundtruth/` (not in git, backed up on the Pi), with its rubric, its windows and Claude's events, plus a review of each window.
  - `tools/label_week.py`: labels a week, two windows (weekday and weekend).
  - `tools/evaluate_grouping.py`: compares grouping methods; to try a method, add it to its table.
  - `/label` keeps only what's taste: rankings and the bar.
- [ ] Look at the hand rules' mistakes on headline-size events (splits and wrong merges) and fix what's systematic.
- [ ] Run `tools/label_week.py` each week. Once there are two weeks, the newest is the test set automatically. Read each window's review before trusting it, and drop weeks that are thin or odd.

**Later, once events hold up**

- Situations as a layer over events (shown together, not merged into one story), anchored on Wikipedia's "Ongoing" list.
- Recalibrate the bar and the ten-story cap from `day_tallies`.
- Revisit learned grouping (the branch) when labels span weeks.
- A better news filter, if it matters (it would need the article body).
- Speed only if needed: about 2 seconds a window on the Mac today.
