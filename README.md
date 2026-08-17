# Altar Cycles — WNC Cycling Calendar

Every cycling event within about 75 miles of the shop, in one place.

**Live: https://calendar.altar.bike** — rebuilds itself every morning at
05:17 ET. Subscribable as `.ics`. Runs on GitHub Actions for free, apart from a
few cents a month in Claude API calls.

| | |
|---|---|
| The calendar | https://calendar.altar.bike |
| Subscribe — everything | https://calendar.altar.bike/events.ics |
| Subscribe — races | https://calendar.altar.bike/races.ics |
| Subscribe — dig days | https://calendar.altar.bike/trail-work.ics |
| Submit an event | https://calendar.altar.bike/submit.html |

The `.ics` links work in Google Calendar, Apple Calendar and Outlook — "add
calendar by URL". That's what goes on altar.bike.

**Running the shop and something looks wrong?** Read
[DEPLOY.md](DEPLOY.md) — it's the day-to-day guide, written for clicking.
**Picking this up as an agent or a new developer?** Read
[CLAUDE.md](CLAUDE.md) first — current state, what's verified versus guessed,
and the task order. It is the most important file here.

---

## How it works

```
sources.yml     15 sources and how to read each one
   ↓
adapters.py     one function per platform, Claude as the last rung
   ↓
normalize.py    expand recurrence, tidy titles, geofence, classify,
                dedupe, cap
   ↓
build.py        cache, merge, write site/
```

Fifteen sources, three shapes. Some publish real structured data, some publish
nothing machine-readable, and some are the same underlying calendar. So each
source is a **ladder**: try the exact feed, fall back to schema.org markup,
fall back to Claude reading the page. The last rung means a site redesign
degrades the data instead of breaking the build.

A source that fails serves its **last good events from cache**, so nothing
disappears from the calendar because someone's website had a bad morning.

### Why an .ics file and not Google Calendar

Writing into Google Calendar means holding sync state — which of 300 events you
already created, which moved, which got cancelled. Every bug there is a
duplicate or a ghost event on somebody's phone.

Publishing a file inverts it. It's regenerated from scratch every night, so
there's no state to corrupt — and Google Calendar can subscribe to it, so you
get Google Calendar anyway, plus Apple Calendar and Outlook, from one file.

---

## Day to day

**Add an event by hand** — edit [`data/manual.yml`](data/manual.yml) and
commit. Highest trust in the system, so it overrides a scraped version of the
same event. Use it for Altar's own rides and for correcting anything that came
through wrong.

Standing rides use `repeat: weekly | biweekly | monthly` with an optional
`repeat_until`. One row becomes many dates. With no `repeat_until` you get the
next 12 and then it stops — deliberate, so a ride that quietly ended doesn't
sit on the calendar until next year.

**Submissions are gated.** The public form files a GitHub issue labelled
`event-submission`. Nothing publishes until you also add the **`approved`**
label; remove the label and it comes back down on the next build.

**Something's missing** — check the Actions run summary or
`site/build-report.json`. Both list every source and what it returned.

**Test locally:**

```bash
python build.py --only darc      # one source
python build.py --offline        # rebuild from cache, no network
python test_pipeline.py          # offline checks
python test_integration.py       # adapter and end-to-end checks
```

Both suites should be green before anything ships.

---

## Sources that look broken but aren't

The build summary always shows a few `down`. These are understood, and they are
written up with dates and evidence in `sources.yml` and `CLAUDE.md`.

| Source | Why |
|---|---|
| Blue Ridge Bicycle Club | Ride calendar is a **paid member benefit**. Not scraping past that. Their public races arrive via BikeReg anyway. |
| Ride with GPS | Closed. **Both** clubs use it as a route library, not an event calendar — AoB's last event there was May 2024, BRBC's is empty. Not a fixable bug. |
| NICA / Pisgah Rage / Ring of Fire | **Seasonal.** NC is a spring league (Jan–Jun, registration opens 1 Nov); the velodrome series runs May–June. Empty the rest of the year by design. |
| RunSignup | Mostly a running-race platform. Publishes here only when a title carries an actual cycling word — without that rule you get turkey trots. |
| IC Imagine | Domain not answering. Decide in November whether to delete it. |

---

## Adding or fixing a source

`probe.py` reports what feed a site actually has:

```bash
python probe.py https://example.org/events
```

Then copy the closest existing block in `sources.yml`. Set `trust`: 100
hand-entered, 80 the org's own site, 60 a registration platform, 40 an
aggregator.

Three things the codebase has learned the hard way, all of them the same
mistake in different clothes:

1. **A source returning zero is not evidence it works.** RunSignup reported
   "0 events" for weeks because it was being sent the wrong date format and the
   error came back as an empty list.
2. **Never trust a filter param you haven't verified.** BikeReg silently
   ignores unrecognised filters and serves the national list, which looks
   exactly like success.
3. **Read the live site, not just the build log.** A craft exhibition published
   eleven times under `blue-ridge-heritage — ok (32 events)` because the
   keyword `trail` matched "Craft Trails".

Every finding is a dated comment in `sources.yml`, and every bug that reached
the live site is pinned as a test with the real title that shipped.

---

## Files

| | |
|---|---|
| `CLAUDE.md` | handoff brief — **read first** |
| `DEPLOY.md` | running it day to day, written for Matt |
| `sources.yml` | the fifteen sources, with dated findings on each |
| `adapters.py` | one function per platform, plus the Claude fallback |
| `normalize.py` | recurrence, titles, categories, geofence, dedupe, caps |
| `build.py` | orchestrates, caches, writes `events.json` and the feeds |
| `probe.py` | "what feed does this site have" |
| `site/index.html` | the calendar |
| `site/submit.html` | the submission form |
| `data/manual.yml` | hand-entered events |
| `worker.js` | optional: submissions → GitHub issues without email |
| `bootstrap.sh` | original one-shot deploy. **Already done** — kept for reference |

---

## Brand

Forge Black `#111111`, Ash White `#CCCCCC`, Altar Rust `#B85C2A`, Trail Earth
`#4A3728`, Pisgah Shadow `#2C4A5A`. Big Shoulders Display 900 for display, Lora
for body, Space Mono for dates and distances.

The year-profile strip at the top of the calendar is the one flourish — event
density drawn as an elevation profile, which is how riders read a route anyway.
Signed off by Sarah Cearley, 2026-08-17.
