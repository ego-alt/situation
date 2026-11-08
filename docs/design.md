# Design

What Situation is for, how it decides what's on the page, how each decision is measured, and what's next. What it does today is in the README; how it looks is in the dashboard's `docs/design.md`.

A home-stack app that tells you when something big is happening in the world, and stays quiet otherwise.

## The problem

- News apps send too many notifications, so they all get swiped away, the important ones included.
- Opening a news app every day never became a habit.
- Twitter surfaces some of it, but only when the algorithm happens to. The French riots (October 2026) went unnoticed until a friend mentioned them.

So the job isn't "more news". It's a **filter with a high bar**, delivered somewhere you'll see it without building a new habit.

## Principles

1. **Few, and only what matters.** A normal day shows five to ten stories out of several hundred. A quiet day shows fewer, and the page says so. Nothing is padded.
2. **Headlines first.** Each story is one line saying what happened, plus the outlets that carry it. Reading more is one tap away, at the source.
3. **Importance comes from evidence, not an algorithm tuned for clicks.** The signals are public and explainable: how many independent newsrooms lead with a story, and whether Wikipedia's editors put it "In the news".
4. **One notification a day, at most one more.** A morning brief, and a second push only for something historic. Never a stream.
5. **Storylines over items.** Ongoing situations (a war, an outbreak, unrest) are tracked as one thing that develops, not re-announced every day.
6. **Works without AI.** Grouping, scoring and tiering run on the Pi with no language model. Claude labels data offline, to measure and train against (below); the live app needs no key.

## Sources

| Kind | Sources | Why |
|---|---|---|
| Wires (Google News search) | Reuters World, AP | The two agencies most newsrooms build on; their own feeds are gone, so they're read through a Google News site search, titles only. |
| World desks (RSS) | BBC World, Guardian World, NYT World, Al Jazeera, France 24, DW, NPR World, Le Monde (English), Euronews, Bloomberg Politics (videos skipped) | Independent newsrooms on different continents; agreement between them is the main signal. |
| Your region (RSS) | SCMP, RTHK, Taipei Times, CNA Asia | Hong Kong, Taiwan and the region get a lower bar. |
| Curated (Wikipedia) | "In the news" and its "Ongoing" list; the Current events portal | Human editors with a high, global bar. ITN adds a few items a day, hours after the event. On 18 Oct 2026 its top item was the Limoges city hall burning in the French protests. |

Twitter/X is out: its API is expensive and its ranking is the problem being solved. Feeds are configured in `sources.py`; each has a weight and a region.

## How importance is decided

Every fetch (every 15 minutes):

1. **Collect** the latest items from every feed: title, summary, link, time and position in the feed. In the four feeds kept in front-page order (BBC, Guardian, NYT, Le Monde), the first items are the lead stories; the rest are newest first.
2. **Cluster** items about the same event. The cluster key is TF-IDF over titles and summaries; items join when they're similar enough within 48 hours. A linked group is then cut into tight cores, and cores stay one story only if they share what happened, not just names: the Wainwright base fire and Alberta's referendum share "Alberta" and "Carney" but not "fire" or "vote", so one article mentioning both can't fuse them. Clusters live across fetches, so a story keeps its identity as it develops.
3. **Score** each cluster:
   - **breadth:** the number of distinct outlets carrying it, weighted per outlet. This is the core signal.
   - **lead:** a bonus when front-page-ordered outlets put it among their top three stories.
   - **curated:** a bonus worth about two outlets when it matches a Wikipedia "In the news" item. It's confirmation, not a trump card: it lifts an under-covered story to Headline, but makes a story Major only alongside real coverage.
   - **region:** a bonus for Hong Kong, Taiwan, China and the UK.
   - **freshness:** full for 8 hours from when the story broke (a second outlet picked it up), then halving every 12 hours, unless three or more outlets are still writing about it; so yesterday's story yields to today's.
4. **Tier** each story:
   - **Major:** carried by six or more outlets with at least two leading with it, or new on ITN (within a day) and carried by four or more. These can trigger the second push.
   - **Headline:** carried by four or more outlets, or three when one of them leads with it; for Hong Kong, Taiwan, China and the UK, a weighted breadth of 2. These are shown on the page and in the brief, at most ten at a time (the best by score).
   - Sport and entertainment stay below the bar, even on ITN, which lists recurring finals.
   - Everything else stays out of sight. It's searchable later, never shown by default.
5. **Running stories** (`situations.py`): many events that are part of one thing, found from the news itself. Words whose use jumps over the last day against the fortnight before (at least 12 articles, 6 outlets, three times the usual rate; never a name that's always in the news, like "Trump") and mostly appear together are one candidate. An event joins when most of its articles use its words and it's near the candidate in meaning; a story stays in one country, needs three events, two of them carried by two or more outlets and none holding more than 70% of its articles (one big event is already a story), and words besides place names ("White House" is where, not what). It's named for a phrase at least three of its events use ("US midterm elections"), as their headlines write it; with none, its words were only shared by the day's stories ("president"), and there's no running story. It's scored over all its articles, as fresh as its newest developments, and FLASH with 8+ outlets and 3+ developments carried by two or more in the last 12 hours. The page shows it once, its events as developments. Within a running story, an article the learned news filter doubts counts when the stricter rules call it news ("US midterms: Democrat wins North Carolina" is how running stories are headlined); election results pages are never news.
6. **Ongoing:** each item in ITN's "Ongoing" list becomes a storyline. A story attaches to it when its headline names one of the situation's countries, it or most of its articles do so in the words of conflict, and it's near the week's coverage in meaning; the page shows its latest development.

## The pipeline: four questions

Every article goes through four decisions, in order. Each has its own ground truth, its own metric, and its own method. A method can be a heuristic, hand-written rules, an off-the-shelf model or a language model, and each stage is chosen and improved on its own.

| Stage | Question | Objective or taste | Ground truth | Metric | Method today |
|---|---|---|---|---|---|
| **1. News** | Is this a report of something happening? | objective | Claude | precision and recall of what's dropped (dropping real news costs more than letting cruft through) | headline rule + `/opinion/` URLs (`score.is_news`) |
| **2. Event** | Do these articles report one thing that happened? | objective | Claude, weekly (`evaluation/groundtruth.py`) | B-cubed, over all articles and headline-size events (`evaluation/metrics.py`) | hand rules (`cluster.by_rules`) |
| **3. Situation** | Are these events part of one running story? | mixed: wars are objective, how wide is taste | Claude with your rubric, plus your checks | is each headline event under the right situation | running stories from bursting words (`situations.py`), and Wikipedia's "Ongoing" list matched by country |
| **4. Tier** | Flash, priority or below, and in what order? | taste | you, on `/label` (rankings, "would you show it?") | ranking and bar agreement; is the top 10 right | hand-set score and thresholds (`score.assess`) |

On the side: **where** each event happens (`geo.locate`, or a place model trained on Claude's places).

Errors flow downstream: a non-news piece that gets through can join an event, a wrong merge inflates an event's outlet count, and that inflates its tier. So each stage is measured on its own, and the page is checked as a whole (is the top 10 right?).

## How it's measured

- **Define the unit before modelling.** Each stage's question, in one sentence, comes before any labels or models.
- **Objective questions get Claude's labels; taste gets yours.** Stages 1–3 are labelled by Claude in bulk; stage 4, and how wide a situation is, by you.
- **Evaluate on production data, raw.** Clean freely inside a model's pipeline, as long as production runs the same cleaning, and in its training data. Never clean the test set in a way the live app can't.
- **Simplest method first; escalate only on a measured need.** Rules, then off-the-shelf, then learned, then a language model.
- **Language models as labellers, rarely in production.** They're right for labelling high-volume objective stages. Live, they're only worth it where volume is low (stages 3–4), and the app must still work without one.
- **Curate datasets, don't just grow them.** Check label quality, report by stratum, and review each new batch before trusting it.
- **Every comparison comes with an interval,** against what's deployed, on the newest data.

### The apparatus

Live code is flat at the top (`fetch`, `pipeline`, `cluster`, `score`, `geo`, `text`, `embed`, `learn`, `labels`, `app`); measuring lives in `evaluation/` and scripts in `tools/`.

- **`evaluation/metrics.py`**: B-cubed, headline-size, F1, bootstrap intervals. Pure functions.
- **`evaluation/groundtruth.py`**: the event ground truth, one file per ISO week in `instance/groundtruth/` (not in git, backed up on the Pi). Each file records its rubric and model, its windows (a weekday and a weekend, news only) and Claude's events, plus a review of each window.
- **`tools/label_week.py`**: labels a week from a database copy through the Batch API (about $1–2). The database keeps 30 days, so any recent week can be labelled after the fact.
- **`tools/evaluate_grouping.py`**: compares event methods against the deployed one on the newest week, with intervals. A new method is one line in its table.
- **`labels.py` and `/label`**: your rankings and bar answers, and their scoring (`flask evaluate`, `flask train`).

#### The labelled data

Three files in `instance/groundtruth/`, each with one job. A piece (`groundtruth.piece`: the same headline under any url) is only ever on one side of a train/test split.

| File | What | Labelled by | Used for |
|---|---|---|---|
| `<week>.json.gz` | Two 48-hour windows of a week, every article: news or not, and the events | Sonnet (`tools/label_week.py`) | **Test**: stage 1 is scored on it (cross-validated), stage 2 too. The newest week no model has seen is the final test. |
| `news-train.json.gz` | Other articles from the database, never the test weeks' pieces: news or not | Sonnet (`tools/label_news.py`) | **Training only**, adds to the folds. Top it up monthly so it spans more than one news cycle. |
| `news-check.json.gz` | A second opinion on the same articles, not more data | Opus (`tools/check_news_labels.py`) | Marks disputed labels: left out of training, and scored separately. |

The shipped model trains on the first two together (disputed labels left out), so its honest score is on a week labelled after it was trained. Hand corrections live in the file they correct, under `corrections` (url, from, to, why), so a relabel can see them.

## Phase 1: learning to group (October 2026)

**Aim.** Make grouping improve from labelled data instead of hand-tuned rules.

**Outcome**
- The non-news filter is in: hand rules, then a learned model (stage 1 below).
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
6. **The feed can tell features from news, the page adds nothing.** About 29% of articles aren't news. The rules (headline and url) catch half at 96% precision; a learned model on the feed alone (rules, format signals, the embedding, outlet and url tokens: 548 features) catches 81% at 89%. The article page's schema.org type and section scored the same as without it, and half the outlets refuse the fetch.
7. **Small, correlated data can't separate methods,** so gaps of a few points sit within noise.

## Roadmap

Ordered by what matters most now. The learning stages come first; then the product, most of which was planned from the start and isn't built yet.

**Stage 4, tier** (what you see; taste)
- [ ] Set the Headline bar and the ten-story cap from the real daily counts in `day_tallies`, rather than two days' snapshots.
- [ ] Rank and answer "would you show it?" on `/label` (40 rankings so far).
- [ ] Train the rank model when the rankings are enough; keep it only if it beats the hand score on held-out labels.

**Stage 1, news** (measure before fixing)
- [x] Learned model on feed evidence (`tools/evaluate_news.py`): 88.7% precision, 81.1% recall, against 96.5 / 51.6 for the rules. Opus and Sonnet agree on 94% of labels, so the ceiling is near 89 / 89.
- [x] Ship (26 Oct): trained on all 5,395 labels, saved as `instance/models/news.json`, and `news_only` drops what it scores at or above the threshold that gave 90% precision (cross-validated: 90.3% precision, 78.4% recall, against 96.5 / 51.6 for the rules).
- [x] Second labeller (26 Oct): Opus agrees with Sonnet on 95.1% (4,605 checked before credit ran out; rerun to finish). Disputed labels are left out of training.
- [x] Label review (26 Oct): the 125 labels the model confidently disputed, read against the rubric: 31 were wrong (newsletters and galleries labelled news, statements and released reports labelled not news). 100 random agreements had 1 error. Scored on corrected labels: 90.1% precision, 81.2% recall (78.7% on the old labels: most of the gain is measuring it right). Model types compared on the same features: logistic regression ties or beats random forest, gradient-boosted trees and XGBoost, so it stays.
- [ ] Final test on a week the model has never seen (W44), once.
- [ ] Check what it changes on screen: the same windows grouped with the rules and with the model, headline-size stories compared.
- [ ] Later: fine-tune the embedding on the labels (on the Mac), then distil it back to a static model so the Pi still only looks up and averages.

**Stage 2, event** (good enough for now)
- [ ] Run `tools/label_week.py` weekly (or monthly for the last four weeks). Read each window's review.
- [ ] Label QA: Opus re-labels a sample of each week's bundles; check or drop the ones they disagree on.
- [ ] Report results by stratum (event size, region, topic), so we see where a method fails and whether the set covers enough of each.
- [ ] Study the hand rules' mistakes on headline-size events, and fix what's systematic.

**Stage 3, situation**
- [x] Running stories from the news itself (4 Nov 2026, tuned on the US midterms): bursting words, joined by co-occurrence, events by words and meaning, one country each. Checked by hand on every day from 26 Oct to 4 Nov: it found the Brazilian and Israeli elections, the Spanish train crash's aftermath and the midterms, and nothing on the quiet days. No labelled ground truth yet.
- [x] Precision pass (6 Nov 2026), replayed every 12 hours from 20 Oct to 6 Nov: no running story where one event holds over 70% of the articles (a sentencing beside other court cases, the train crash), none made only of place words, and none without a phrase three of its events share ("White House", "US president", a day's Ukraine talks, campaign and riots chained into one). 44 detections became 16, all real; France's riots after 21 Oct and Italy's small protests were lost.
- [ ] Label running stories (Claude, a week at a time) and measure membership, rather than eyeballing ten days.
- [ ] Join Wikipedia's "Ongoing" list and running stories into one list of situations.

(once events hold up)
- [ ] Situations as a layer over events (shown together, not merged), anchored on Wikipedia's "Ongoing" list.

**The product** (not built yet)
- **Delivery through ntfy, with quiet hours.** A morning brief (one push at a set time with the top three to five headlines and a link; none on a day with nothing at Headline or above), and a Major alert (at most one a day, outside quiet hours, only for Major stories that weren't in the brief). Stories that were shown are closed rather than deleted, keeping their ids, so nothing will be pushed twice.
- **Claude's headlines** (optional, `ANTHROPIC_API_KEY`): for each Headline-or-higher story, a neutral headline of 12 words or fewer, a "why it matters" line, and a 1-to-5 significance rating that can move a story one tier at most. Without a key, the most-shared outlet title stays.
- **The dashboard tile shows the top headline**, so it's visible wherever you already look.
- **Later:** a weekly recap, an MCP tool ("what's going on in the world?"), per-region bars you can tune, and timelines for each ongoing situation.

**Decisions for you** (when delivery is built)
- **Brief time and quiet hours.** The defaults are a 08:00 brief and quiet hours from 22:00 to 08:00, in your local time.
- **Notifications through ntfy?** It's self-hostable and works on Android without Google services; the public ntfy.sh with a private topic name is fine to start.
- **Regions.** The default boosts are Hong Kong, Taiwan, China and the UK. Add or remove any.
- **Outlets.** Add an outlet you trust or drop one you don't.

**Later, for learning**
- A small historical "challenge set" for rare cases (results, ceasefires, leaders falling), if live weeks don't cover them.
- Revisit learned grouping (the branch) when labels span weeks.
- Speed only if needed (about 2 seconds a window on the Mac).
