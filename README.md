# Altar Cycles — WNC Cycling Calendar

Pulls cycling events from thirteen sources, merges them into one calendar, and
publishes it as a web page plus a subscribable `.ics` feed.

Runs itself daily on GitHub Actions. Costs nothing except a few cents a month
in Claude API calls.

> Picking this up in Cowork or a fresh session? Read **`CLAUDE.md`** first —
> it has current state, what's verified vs. guessed, and the task order.

---

## Setup

```bash
cd altar-events
./bootstrap.sh
```

That creates the repo, pushes, sets your API key as a secret, enables GitHub
Pages, and kicks off the first build. It's safe to re-run. You need `git` and
`gh` (`brew install gh`, then `gh auth login`) and your Anthropic API key.

Then the one step that still needs you:

```bash
python3 -m pip install -r requirements.txt
python3 probe.py --check
```

**Don't skip this.** It tests all thirteen sources against the live sites and
prints which endpoints are wrong. I couldn't reach those domains from the
sandbox I built this in, so several are still educated guesses. For anything
that says FAIL:

```bash
python3 probe.py https://darccycling.com/calendar
```

It reports the platform, any declared feeds, and every endpoint that returned
events. Put the winner in `sources.yml`, commit, done.

### Point altar.bike at it

**Embed** — the page is responsive and self-contained:

```html
<iframe src="https://altarcycles.github.io/altar-events/"
        style="width:100%;height:1400px;border:0" title="WNC Cycling Calendar"></iframe>
```

**Subdomain** (better — no iframe scroll weirdness): `bootstrap.sh` already
sets this up. It writes `site/CNAME` and registers `calendar.altar.bike` as the
Pages custom domain, so the only part left for you is the DNS record at the
registrar:

```
type    CNAME
name    calendar
value   altarcycles.github.io
```

Both halves are needed. `site/CNAME` is what stops the custom domain being
dropped on the next deploy; the Pages setting is what makes GitHub answer for
that hostname. To stay on the github.io address instead, run
`DOMAIN= ./bootstrap.sh`. Turn on **Enforce HTTPS** under Settings → Pages once
DNS resolves — the certificate can take up to an hour.

### Subscribe on your phone

Google Calendar → **Other calendars → + → From URL**:

```
https://calendar.altar.bike/events.ics
```

Google re-reads it every few hours by itself. Same URL works in Apple Calendar
and Outlook. Three feeds: `events.ics` (everything), `races.ics`,
`trail-work.ics`.

### Submissions (optional)

Out of the box the form opens a pre-filled email, which works. For a real
queue, deploy `worker.js` to Cloudflare Workers (instructions in the file) and
paste the worker URL into `ENDPOINT` at the top of `site/submit.html`.
Submissions become GitHub issues — **add the `approved` label and the next
build publishes it.**

---

## Why it's built this way

**Why not store events in Google Calendar?** You suggested it, and I went the
other way. Writing to Google Calendar means holding sync state — knowing which
of the 300 events you already created, updating the ones that moved, deleting
the ones that got cancelled. Every bug there is a duplicate or a ghost event
on someone's phone.

Publishing an `.ics` file inverts it. The file is regenerated from scratch
every night, so there's no state to corrupt. And Google Calendar can
*subscribe* to it — so you still get a Google Calendar, plus Apple Calendar,
Outlook, and anything else, from the same file. Step 6 takes ten seconds.

**Nine sources, three shapes.** Three of the sites you listed publish real
structured data, three publish nothing machine-readable, and three are the
same underlying calendar. So the fetcher is a ladder: try the real feed, fall
back to schema.org markup, fall back to Claude reading the page. The last rung
means a site redesign degrades the data slightly instead of breaking the build.

**The two sources you didn't list matter most.** BikeReg and RunSignup handle
registration for nearly every regional race. They'll surface the Pisgah Stage
Race, Old Fort Fifty, Dirt Diggler, NCCX and the UCI weekend at Rock Creek
weeks before the promoters update their own sites.

---

## What I verified, and what I didn't

Honest inventory, because the difference matters when something fails.

| Source | Platform | How | Status |
|---|---|---|---|
| Pisgah Area SORBA | Squarespace | `?format=json` | **Confirmed** |
| DARC | GoDaddy Builder | Claude reads it | **Confirmed** — hand-typed prose, no feed exists |
| RunSignup | — | public REST | **Confirmed** — documented, no key needed |
| Asheville on Bikes | WordPress | Claude reads hub + event pages | **Corrected** — there is no Events Calendar feed; `/events` is a static annual list |
| AoB community rides | Ride with GPS | v1 API | **Corrected** — needs an API key, list renders in JS. Inert until `RWGPS_API_KEY` is set |
| G5 Trail Collective | Wix | JSON-LD → Claude | Likely — Wix confirmed, markup inferred |
| Blue Ridge Bicycle Club | ClubExpress | iCal handler | Likely — club_id 285841 confirmed, feed path inferred |
| NICA NC | WordPress | JSON-LD → Claude | Likely |
| BikeReg | — | GraphQL search | **Unverified** — the API is real and documented, the endpoint URL is a guess. Falls back to Claude reading the state listing |

`python probe.py --check` resolves every "likely" and "unverified" row in
about a minute.

Two more things worth knowing:

- **Pisgah Rage and IC Imagine are NICA teams, not race promoters.** Their
  calendars are the NICA NC league schedule plus team-only practices. They're
  configured at low trust so league listings win, and practices get filtered
  out. Not much unique signal there.
- **UCI is in a separate bucket.** Nobody plans a Saturday around a World Cup
  in Andorra. Those events are kept out of the local calendar and tagged
  `watch` — useful if you ever want a "showing it at the shop" list.

---

## Running it day to day

**Add something by hand** — edit `data/manual.yml`, commit. Highest trust, so
it overrides a scraped version of the same event. Use it for Altar's own rides
and for fixing anything that came through wrong.

**Something's missing** — check `site/build-report.json`, or the Actions run
summary, which lists every source and what it returned.

**A source broke** — the build keeps serving that source's last good events
from cache, so nothing disappears. Fix it when you get to it:
`python probe.py <their events page>`.

**Test locally without touching anything:**

```bash
python build.py --only darc      # one source
python build.py --offline        # rebuild from cache, no network
python test_pipeline.py          # 38 checks
python test_integration.py       # 30 checks
```

---

## Tuning

Everything lives in `sources.yml`.

- **Too many club rides?** Lower `max_per_week` on Blue Ridge Bicycle Club.
  It's at 4 — without a cap they're roughly 70% of the calendar.
- **Wrong radius?** `defaults.radius_miles`, currently 75. Events outside it
  are dropped unless the source is bucketed `world`.
- **Add a source?** Run `probe.py` against it, then copy the closest existing
  block. Set `trust` — 80 for the org's own site, 60 for a registration
  platform, 40 for an aggregator.
- **Something miscategorised?** `CATEGORY_RULES` in `normalize.py`. Matching
  is on word boundaries, not substrings — that's deliberate. A bare `camp`
  once filed the Old Fort Fifty as a clinic, because it starts at Camp Grier.

---

## Files

```
CLAUDE.md           handoff brief — read first in a new session
bootstrap.sh        one-shot deploy: repo, secrets, Pages, first build
sources.yml         the thirteen sources and how to read each one
adapters.py         one function per platform, plus the Claude fallback
normalize.py        categorise, geofence, cap, dedupe
build.py            orchestrates, caches, writes events.json + the .ics feeds
probe.py            "what feed does this site have" — run when adding or fixing
site/index.html     the calendar
site/submit.html    the submission form
worker.js           optional: submissions → GitHub issues
data/manual.yml     hand-entered events
```

Brand: Forge Black `#111111`, Ash White `#CCCCCC`, Altar Rust `#B85C2A`,
Trail Earth `#4A3728`, Pisgah Shadow `#2C4A5A`. Big Shoulders Display 900 for
display, Lora for body, Space Mono for dates and distances. The year-profile
strip at the top of the calendar is the one flourish — event density drawn as
an elevation profile, which is how riders read a route anyway. Sarah should
sign off on it before it goes on altar.bike.
