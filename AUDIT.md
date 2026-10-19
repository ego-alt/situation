# Situation: product audit

A review of Situation a day after it went live on the stack. Its job, from `docs/plan.md`: **a filter with a high bar**. It should tell you when something big is happening in the world and stay quiet otherwise, on the phone, without needing a new habit. So the questions are whether the bar is right, whether you can trust what the page shows, and whether it works on a phone.

**How this was tested.**
- I read the whole codebase: fetch, cluster, geo, score, pipeline, the app, the page's JS and CSS, and the tests.
- I probed all 14 feeds from the Pi and inspected the live database, then ran the app locally on a copy of it: 411 stories from 14 outlets, of which 3 were FLASH and 12 PRIORITY.
- I walked through it in Chrome at 1280×800 with a mouse, and at 375×667 with touch emulation.
- I tried failure cases: the API unreachable and the map file missing.
- I checked how each feed orders its items, using the 18 October snapshot.
- The 12 tests pass. Nothing in the repo was changed.

**Tags:** **[confirmed]** = seen in the running app or the live data. **[code]** = from reading the code, not reproduced.

**What already works well:**
- The French riots are one story across 12 outlets, and its source list reads like a briefing.
- Headlines are clean, with live blogs and emoji skipped.
- There are no console errors and no horizontal scroll on a phone, and the pulsing respects reduced motion.
- Adding it to the stack was smooth.

---

## 1. The filter: is the bar right?

**1.1 The "lead story" signal mostly measures recency** [confirmed]
- **What:** A story gets a bonus, and can reach FLASH, when outlets carry it among their top three items. That only means "lead story" if the feed is ordered by editors. In the 18 October snapshot, 10 of the 14 feeds were plain newest-first: Al Jazeera, France 24, DW, NPR, Euronews, both SCMP feeds, RTHK and CNA, and Taipei Times wasn't in that snapshot. So for most outlets, "led with it" means "published it in the last hour or so".
- **Where:** `score.py` `assess` (`position < 3`); `sources.py`
- **Why:** FLASH is meant to mean several newsrooms put this at the top. At the moment, two fresh wire rewrites can count as that. It undermines principle 3: importance from evidence.
- **Fix:**
  - Add `ordered=True` to the sources whose feeds are front-page order (BBC, Guardian, NYT, Le Monde), and count leads only from those.
  - Where an outlet has a "top stories" feed, use it as the ordered source.

**1.2 It shows more than the promised five to ten** [confirmed]
- **What:** The live page showed 15 stories (3 FLASH, 12 PRIORITY). The plan and README say "a normal day shows five to ten".
- **Where:** `score.py` tiers (`outlets >= 4`, or `>= 3` with a lead)
- **Why:** Every extra row makes the important ones easier to skim past. That's the exact problem the app exists to fix. Part of the excess comes from 1.1 (cheap leads), and part from the DW "all" feed (135 items, including culture and sport).
- **Fix:**
  - Fix 1.1 first.
  - Use DW's world feed instead of "all".
  - Log the daily counts for a week, then set the bar from that data rather than from one day. A cap (say ten, by score) is a reasonable backstop.

**1.3 A broken feed fails silently** [code]
- **What:** If a feed errors or starts returning nothing, `fetch_source` returns an empty list and nothing records it. All 14 feeds answer today. But a renamed URL would quietly drop an outlet for weeks.
- **Where:** `fetch.py` `fetch_source`
- **Why:** The bar is "N outlets". With one outlet missing, every story needs a larger share of the remaining outlets, and the page would look like a quiet news week.
- **Fix:**
  - Record each source's last success and item count.
  - Show "14/14 sources" in the status bar, turning amber when a feed has been silent for 6 hours.

**1.4 The place lookup is tuned to one day** [code]
- **What:** Alongside its country tables, `geo.py` maps hand-picked names and places to countries, all taken from the test day's stories: Macron, Limoges, Zelensky, Netanyahu, Hamas, John Lee. Other leaders aren't there; tomorrow's equivalents won't be. Ambiguous names (Georgia, Jordan) match by spelling alone.
- **Where:** `geo.py` `ALIASES`, `EXTRA`
- **Why:** A misplaced or unplaced story loses its pin and its region boost. A Hong Kong story that only names a minister gets no lower bar.
- **Fix:**
  - Replace the ad-hoc names with a small, reviewed table: heads of state and government, and capitals.
  - Add a second snapshot day to the tests, so tuning can't overfit one day.

**1.5 FLASH from Wikipedia lasts as long as the item stays up** [code]
- **What:** Any story matching an "In the news" item is FLASH. Items stay in that box for days, so a story keeps FLASH for as long as any outlet keeps updating it.
- **Where:** `score.py` `assess` (`if itn_item`)
- **Why:** FLASH should mean "new and big". The planned "major alert" push will lean on this.
- **Fix:** Count the Wikipedia signal for FLASH only within 24 hours of the item first appearing. The plan already calls for tracking when items first appear. After that, it's still a PRIORITY boost.

## 2. On the phone

**2.1 Map pins are about 4 px wide** [confirmed]
- **What:** At 375 px the map is 375×200, and pins render 3.7 px across. Only FLASH pins have a label. PRIORITY pins rely on a hover tooltip, and phones don't hover.
- **Where:** `situation.js` `drawPins`; `situation.css` `.map`
- **Why:** The map is the centrepiece, but on the device you'll use most it's decoration: you can't tell what a dot is, or hit it reliably.
- **Fix:**
  - Give every pin an invisible hit circle of about 22 px (in screen pixels) and a minimum drawn size.
  - On narrow screens, label every pin with its country code.
  - Consider cropping the phone map to the area with pins.

**2.2 Two stories in one country sit on the same spot** [confirmed]
- **What:** Pins go at the country's centre, so stories in the same country overlap exactly. Live, the IL PRIORITY pin was hidden under the IL FLASH pin, and the same happened to the two CA stories.
- **Where:** `geo.py` `locate`; `situation.js` `drawPins`
- **Why:** The hidden story can't be found from the map.
- **Fix:** Draw one pin per country, sized by its biggest story, with a count badge. Tapping it scrolls to that country's stories, or filters the list to them.

**2.3 The status bar takes three lines** [confirmed]
- **What:** On a phone the readouts wrap: SITUATION, then the clocks, SYNC and FLASH, then PRIORITY on a line of its own. That's 94 px before the map.
- **Where:** `situation.css` `.bar`, `.readouts`
- **Fix:** On narrow screens, keep SYNC and the two counts on one line, and drop the clocks or move them below the map.

## 3. When something goes wrong

**3.1 Signing out goes unnoticed** [code]
- **What:** Library, calendar and tapes load the stack's `signed_out.js`. When the session ends, nginx answers their API calls with 401 and `X-Signed-Out`, and they offer "Sign in again". Situation doesn't load it. Its refresh gets the 401 and shows `SYNC offline`, and the stories on screen stay up, looking current.
- **Where:** `templates/index.html`; `situation.js` `refresh`
- **Fix:** Include `signed_out.js` the way the other apps do, and handle the 401 in `refresh`.

**3.2 Old news looks like current news** [confirmed]
- **What:**
  - If the server can't be reached, the only sign is the word "offline" in the small SYNC readout, and the stories stay as they were.
  - On first load with the API down, the page says "Acquiring…" for ever.
  - On the server, `/healthz` answers OK even when no fetch has succeeded for hours. So the Deploy tab, Monitor and nginx all report it as healthy.
- **Where:** `situation.js` `refresh`, `start`; `app.py` `healthz`
- **Why:** For a "did I miss something?" app, stale data presented as current is the worst failure.
- **Fix:**
  - Show a banner when data is older than 30 minutes or the server is unreachable: *Showing news from 14:05; can't reach the server.* Give the first load a proper error with a retry.
  - Have `/healthz` return 503 when the last successful fetch is over an hour old.

**3.3 If the map fails, the news doesn't load** [confirmed]
- **What:** The page waits for `world.json` before fetching stories, so a failed map load leaves the whole page on "Acquiring…".
- **Where:** `situation.js` `start`
- **Fix:** Load the map and the stories in parallel. The list should render even without the map.

## 4. What the page shows

**4.1 "Earlier this week" can only reach back two days** [code]
- **What:** Every fetch re-groups the last 48 hours of articles and **deletes** any story with no articles left in that window. So "Earlier this week" never holds anything older than 48 hours, despite the plan's promise that story headlines are kept for a weekly view.
- **Where:** `pipeline.py` `regroup` (`for sid in set(old) - claimed: db.session.delete`)
- **Why:** It also means story ids don't last, which the morning brief and major alerts will need so they never push the same story twice.
- **Fix:** Close stories instead of deleting them: keep their headline, tier and newest time. Show seven days of PRIORITY and FLASH under "Earlier this week".

**4.2 Active situations with nothing under them** [confirmed]
- **What:** "Ethiopian offensive" and "Iran war" were listed with no line under them, because no story there had two outlets yet. The rows can't be tapped either.
- **Where:** `pipeline.py` `ongoing_situations`; `situation.js` `drawFeed`
- **Fix:**
  - Show the latest report even if only one outlet carries it, marked as such, or "No new reports today".
  - Make each row tappable, to show that situation's stories.

**4.3 Wikipedia's photo notes leak into its items** [confirmed]
- **What:** Live, one item reads *"The Hôtel de Ville in Limoges is partially destroyed by arson (damage pictured)…"*. Only the exact text " (pictured)" is removed.
- **Where:** `fetch.py` `parse_itn`
- **Fix:** Strip any parenthetical ending in "pictured".

## 5. Security and running it

**5.1 Feed links aren't checked before they become links** [code]
- **What:** Each source's URL goes into `href` with HTML escaping only. A `javascript:` URL in a feed would run when you tap that source. It would run on the stack's origin with your session, and so could call the other apps' APIs.
- **Where:** `fetch.py` `fetch_source` (`url = entry.get("link")`); `situation.js` `itemHtml`
- **Why:** The outlets are reputable, but feeds get hijacked and Situation reads 14 of them. It's cheap to close.
- **Fix:** Keep only `http` and `https` links when fetching, and check again in the page before building the link.

**5.2 The URL prefix is put into a script with HTML escaping** [code]
- **What:** `window.APP_ROOT = "{{ request.script_root }}"`. The value comes from nginx's `X-Forwarded-Prefix`, so it's trusted today. But HTML escaping isn't JavaScript escaping.
- **Where:** `templates/index.html`
- **Fix:** Use `{{ request.script_root|tojson }}`.

**5.3 The container installs dev tools on every start, and runs as root** [confirmed]
- **What:**
  - The start command is `uv run gunicorn…`, which syncs the dev group as well. The Pi's log shows it downloading ruff and pygments each time the container starts. Startup therefore needs the network and is slower, and the running image carries dev tools.
  - The process runs as root, like library, calendar and tapes. The dashboard and mcp run as uid 10001.
- **Where:** `Dockerfile` `CMD`
- **Fix:**
  - Start with `uv run --no-dev --frozen` or `.venv/bin/gunicorn`.
  - Add a non-root user, and do the same for the other apps. Each needs a one-off `chown` of its data folder on the Pi.

**5.4 No container healthcheck** [confirmed]
- **What:** Monitor shows Situation as "Up" without "(healthy)", unlike the others, so a hung app looks fine.
- **Where:** `Dockerfile`
- **Fix:** Add a `HEALTHCHECK` that requests `/healthz` with Python's urllib (the image has no curl). This becomes meaningful once 3.2 makes `/healthz` reflect fetch health.

**5.5 Wikipedia is stored on every fetch, changed or not** [confirmed]
- **What:** The live database had 28 copies of the "In the news" box after 7 hours. That's 96 a day, kept for 30 days.
- **Where:** `pipeline.py` `save_itn`
- **Fix:** Store a copy only when it differs from the last one.

## 6. Small things

- **6.1** `Source.region` is set on five feeds but never read: the regional boost comes from the story's place (`HOME_REGIONS`). Remove it, or use it. [code]
- **6.2** `flask brief --dry-run` is on by default and does nothing. It's a placeholder until delivery (plan phase 3), so remove it until then. [code]
- **6.3** Map pins can take keyboard focus, but Enter does nothing and there's no visible focus ring. [code]

---

## Suggested order

1. **5.1, 5.2, 5.3:** small and safe.
2. **3.1–3.3:** you need to be able to trust that what's on screen is current.
3. **1.1, 1.3, then 1.2 after a week of logged counts:** this is the bar itself.
4. **2.1–2.3:** the map on the phone.
5. **4.1:** before delivery (phase 3), because the brief and alerts need lasting story ids.
6. **The rest.**
