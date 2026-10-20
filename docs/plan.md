# Situation: plan

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
6. **Works without AI; better with it.** Ranking and clustering are deterministic. Claude, when a key is set, only writes neutral one-line headlines and checks the importance call.

## Sources

| Kind | Sources | Why |
|---|---|---|
| World desks (RSS) | BBC World, Guardian World, NYT World, Al Jazeera, France 24, DW, NPR World, Le Monde (English), Euronews | Independent newsrooms on different continents; agreement between them is the main signal. |
| Your region (RSS) | SCMP, RTHK, Taipei Times, CNA Asia | Hong Kong, Taiwan and the region get a lower bar. |
| Curated (Wikipedia) | "In the news" and its "Ongoing" list; the Current events portal | Human editors with a high, global bar. ITN adds a few items a day, hours after the event. On 18 Oct 2026 its top item was the Limoges city hall burning in the French protests. |

Twitter/X is out: its API is expensive and its ranking is the problem being solved. Feeds are configured in `sources.py`; each has a weight and a region.

## How importance is decided

Every fetch (every 15 minutes):

1. **Collect** the latest items from every feed: title, summary, link, time and position in the feed. In the four feeds kept in front-page order (BBC, Guardian, NYT, Le Monde), the first items are the lead stories; the rest are newest first.
2. **Cluster** items about the same event. The cluster key is TF-IDF over titles and summaries; items join when they're similar enough within 48 hours. Clusters live across fetches, so a story keeps its identity as it develops.
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
5. **Ongoing:** each item in ITN's "Ongoing" list becomes a storyline. Clusters that match it attach to it, and the page shows its latest development.

**With Claude (optional, `ANTHROPIC_API_KEY`):** for each Headline-or-higher story, one call writes a neutral headline of 12 words or fewer plus a "why it matters" line, and rates significance from 1 to 5 against a written rubric. The rating can move a story up or down one tier, never more. Without a key, the most-shared outlet title is used as is.

## The look

An operations-room wall: open-source intelligence, not a newspaper.

- **The world map is the centrepiece.** It uses the Equal Earth projection, drawn from Natural Earth's public-domain countries with Antarctica dropped. Each story is pinned where it happens (`geo.py`: an offline lookup of country names, demonyms, capitals and newsworthy cities). Pin size is the number of outlets.
- **Precedence, borrowed from military messaging.** Major stories are **FLASH** (amber, pulsing) and Headline stories are **PRIORITY** (cyan); their countries are tinted.
- **A status bar** with UTC and local clocks, time since the last sync, and counts per tier.
- **Type:** facts (codes, times, counts, outlet names) in IBM Plex Mono, headlines in Inter so they read at a glance.
- **On a phone** the map sits on top and the list follows.
- **Zooming in** shows regions, cities and the places each story names (see the README). Picking a country, place or situation drives both the map and the list; moving the map never changes the list.

## Delivery

- **The page** (`/situation/`, phone-first): Major and Headline stories from the last 24 hours, then the Ongoing storylines, then "Earlier this week". Every story lists its outlets as links.
- **Morning brief** (ntfy push, optional): one notification at a set time, with the top three to five headlines and a link to the page. Not sent on a day with nothing at Headline or above.
- **Major alert** (ntfy, optional): at most one a day, outside quiet hours, only for Major stories that weren't in the morning brief.
- **Dashboard tile:** the dashboard's Services page shows the top headline, so it's visible wherever you already look.
- **Later:** an MCP tool, so an assistant can answer "what's going on in the world?".

## Data

- `articles`: one per feed item (url unique, source, title, summary, published, position, fetched).
- `stories`: a cluster (headline, first and last seen, score, tier, region, ITN match, Claude fields).
- `story_articles`: which articles are in which story.
- `itn_items`: Wikipedia ITN entries and Ongoing topics, with when they were first seen.
- `notifications`: what was pushed and when, so nothing is pushed twice.

Data older than 30 days is pruned; story headlines are kept for the weekly view.

## Phases

1. **Scaffold (done):** fetch the RSS and ITN sources; cluster, place, score and tier; the map page; the background fetch loop; CLI commands `fetch` and `brief`; tests on a real snapshot from 18 Oct 2026.
2. **Live (done):** join the stack (a GitHub repo, a compose service, nginx `/situation/`, the dashboard tile and the auto-deploy), then the audit's fixes (October 2026; the audit itself is in git history) and zoomable regional maps.
   - Still open from the audit: once `day_tallies` holds a week or two, set the Headline bar and the ten-story cap from the real daily counts rather than two days' snapshots.
3. **Delivery:** morning brief and Major alerts through ntfy, with quiet hours.
4. **Claude:** neutral headlines, "why it matters", and the significance check.
5. **Later:** a weekly recap, the MCP tool, "mark as seen", per-region bars you can tune.

## Decisions for you

- **Brief time and quiet hours.** The defaults are a 08:00 brief and quiet hours from 22:00 to 08:00, in your local time.
- **Notifications through ntfy?** It's self-hostable and works on Android without Google services; the public ntfy.sh with a private topic name is fine to start.
- **Regions.** The default boosts are Hong Kong, Taiwan, China and the UK. Add or remove any.
- **Outlets.** Add an outlet you trust or drop one you don't.
