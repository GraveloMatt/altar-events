# Altar Cycles — WNC cycling calendar

**Handoff brief. Read this first.** Third revision. The previous two each
carried one wrong assumption about the environment that cost most of a
session; see "Environment reality" below and **verify, do not trust**.

Project: aggregate every cycling event within 75 miles of the shop into one
calendar, published at `calendar.altar.bike`, embedded on altar.bike, and
subscribable as `.ics`. Built and tested. **Not yet deployed.**

---

## Who you are talking to

Matthew Ball, owner. **He is on Windows and is not a command-line user** — he
did not know what `brew` was, and "run `./bootstrap.sh`" did not parse for him.
This matters:

- Do not lead with terminal commands. Offer the click-through path on
  github.com first, and say plainly which parts need a terminal and which do
  not.
- When a terminal really is required, say where the window comes from (Git Bash,
  installed with Git for Windows) and use `python`, not `python3`.
- Matt Walsh handles the website and is the right person to hand terminal steps
  to. Suggest that rather than teaching a toolchain, unless Matt Ball wants to
  learn it.

---

## Environment reality — read before planning anything

Each session has been different. **Probe it, don't assume.** As of 2026-08-17
(Cowork cloud session):

```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://darccycling.com   # 000/403 = blocked
curl -sS -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/user
```

- **Shell egress to event sources is BLOCKED.** An HTTP proxy sits in front of
  everything (`https_proxy=http://127.0.0.1:...`) and it allowlists package
  registries, github.com and api.github.com only. Everything else returns
  `CONNECT tunnel failed, response 403`. **`probe.py --check` cannot run here.**
- **`web_search` / `web_fetch` DO work** — they sit on a different network path
  and reach live sites fine. Every source verification in this file was done
  that way. Two gotchas: `web_fetch` truncates long JSON bodies before
  summarising, so **any count it reports is a floor, not a total**; and it
  honours robots.txt, which blocks some URLs outright.
- **GitHub identity exists but is read-only and unbound.** `$GH_TOKEN` is
  proxy-injected and `api.github.com/user` answers as **GraveloMatt**. But
  `/user/repos` returns *"sessions are bound to their configured repositories"*
  and every `repos/GraveloMatt/*` path returns 403, so there is no repo to push
  to. `git ls-remote` fails on credentials. **You still cannot create the repo,
  push, set secrets or enable Pages.** Do not promise Matt that you can.
- `gh` is not installed. There is no `ANTHROPIC_API_KEY` in the environment.
- **Do not ask Matt to paste a token or API key into chat.** Direct him to
  enter secrets himself in GitHub's UI.

---

## State of the code

Two suites, both green (2026-08-17). Run them after any change:

```bash
python test_pipeline.py      # 70 checks
python test_integration.py   # 44 checks
```

`sources.yml` (registry) -> `adapters.py` (one function per platform) ->
`normalize.py` (geofence, classify, dedupe, weekly cap) -> `build.py` (writes
`site/`). `probe.py` discovers endpoints. `bootstrap.sh` deploys.
`data/manual.yml` holds the shop's hand-entered events at trust 100 — currently
empty, and the escape hatch when scraping gets something wrong.

Config: home 35.5951 / -82.5515, radius 75 mi, horizon 400 days.

`build.py` degrades correctly: run with no network and no keys at all and it
still exits 0, writes every `site/` file, and lists each failure under
"needs attention". Verified 2026-08-17.

## Source inventory — 13 sources

| id | adapter | trust | status |
|---|---|---|---|
| asheville-on-bikes | llm | 80 | needs `ANTHROPIC_API_KEY` |
| asheville-on-bikes-rwgps | ridewithgps | 75 | needs `RWGPS_API_KEY`, optional |
| darc | llm | 80 | needs `ANTHROPIC_API_KEY` |
| blue-ridge-bicycle-club | clubexpress | 80 | **BLOCKED**, optional |
| pisgah-area-sorba | squarespace | 80 | **STALE — expect zero**, optional |
| g5-trail-collective | wix | 80 | **VERIFIED working** |
| nica-nc | jsonld | 80 | domain confirmed; empty Jul-Nov by design |
| pisgah-rage | llm | 50 | seasonal, mirrors NICA |
| ic-imagine-cycling | llm | 50 | seasonal, optional |
| bikereg | bikereg | 60 | **VERIFIED against real API docs** |
| runsignup | runsignup | 60 | **VERIFIED; coord bug fixed** |
| uci | llm | 60 | cosmetic, optional |
| blue-ridge-heritage | tribe | 40 | **VERIFIED, route serves** |

Every finding below is also a dated comment in `sources.yml`. House rule:
**every failure gets understood and written down, never silently dropped.**
Keep that up.

### Verified 2026-08-17 (second pass)

**BikeReg — fully resolved. Real API docs exist.**
`https://www.bikereg.com/api/EventSearchDoc.aspx` is genuine documentation —
read it before touching this adapter. Documented params: `name`, `region`,
`states`, `loc`, `distance`, `eventtype`, `permit`, `startpage`, `year`,
`startDate`, `endDate`, `eventID`. Confirmed by probing:

| param | verdict |
|---|---|
| `loc=<lat>\|<lng>` + `distance=<mi>` | **works** — exact radius, rows carry a `Distance` |
| `startDate`/`endDate` as `MM/DD/YYYY` | **works** |
| `states=NC` | works |
| `region=Southeast` | works — **this is the region NC is in** |
| `region=Mid Atlantic`, `New England` | work — **spaces, not hyphens** |
| `region=Mid-Atlantic` (hyphen) | **silently ignored** |
| `region=North Carolina` | **silently ignored** |

The hyphenated spelling in site URLs like `/events/Cyclocross/Mid-Atlantic` is
a web route slug, not the API value. The adapter now leads with
`loc`+`distance` (exact radius beats a coarse region bucket — `Southeast`
drags in Maryland) and keeps region/states as fallback rungs. Docs say results
cap at 100 rows by date, so it pages on `startpage`, deduping on `EventId` so
a server that ignores paging can't loop. The self-verifying ladder stays: any
rung whose response contains nothing near Asheville is rejected, because an
unrecognised param returns the **national list, which looks like success**.

**RunSignup — was silently losing events. Fixed.** The endpoint answers
anonymously and `zipcode`+`radius` *are* honoured server-side (radius=5 around
28801 returned only Asheville/Woodfin; radius=100 reached Statesville and
Jonesborough TN). But **the payload contains no coordinates at all** — no
`latitude`, `longitude`, `lat`, `lng` or `coord` field anywhere, only
`address.street/city/state/zipcode`. The adapter read `race["latitude"]`,
always `None`, so every event fell through to `normalize`'s `REGION_TOWNS`
name match and any race in a town not on that list — Statesville, Troutman,
Morristown — was dropped despite the server having already confirmed it was in
radius. Adapters that pass home+radius to an API *and verify it is honoured*
now stamp `pre_geofenced: True`, which `in_region()` accepts. Real coordinates
still overrule the flag. **The old test fixture invented lat/lng inside
`address`, which is exactly what hid this** — it now uses the real captured
shape. Watch for that pattern elsewhere.

**Blue Ridge Heritage — route confirmed, no longer inferred.**
`/wp-json/tribe/events/v1/events` was fetched live and returned real JSON
events. It serves despite the public archive slug being `/calendar/`. No change
needed. Note the sample was all craft/heritage programming, so
`require_keywords` is carrying the load — expect a handful of events at most.

**Pisgah Area SORBA — stale page, wrong system of record. Expect zero.** The
page is reachable but the furthest-future event on it is Bracken Dig Day,
**21 Feb 2026** — six months past. Nothing after Feb 2026 is listed at all.
The page explains why: *"When registering for an event, you will be asked to
create an account on VolunteerHub"* — PAS runs its real calendar in
VolunteerHub and the Squarespace page is a hand-maintained shop window nobody
has refreshed. Also `?format=json`, the entire basis of the `squarespace`
adapter, is **disallowed by their robots.txt**. The obvious tenant guess
`pisgahareasorba.volunteerhub.com` 404s and no search surfaced the real slug.
Marked `optional` with an `llm` fallback so it can't fail the build and picks
up automatically if they refresh. **Cheap human next step:** click any "click
Here!" link on their events page and read the VolunteerHub domain off the
address bar — VolunteerHub exposes iCal, which would turn this into an exact
`ics` source.

### Verified earlier on 2026-08-17 (first pass — still current)

**G5 Trail Collective — works.** Wix Events confirmed. `/volunteer` renders the
full list server-side with title, date, start and end time including year, full
street address and a project description, so the `llm` rung has clean text even
if JSON-LD is absent. Six events found through Nov 2026. Its multi-day entry
(Fall Trail Weekend, Nov 6-8) is the best available exercise of end-date
handling. Real titles are pinned as classification regressions in
`test_pipeline.py`.

**Blue Ridge Bicycle Club — blocked, stop chasing it.** ClubExpress publishes no
club-wide iCal feed: their docs list only calendar search filters, and their
user guide describes ICS as per-event "Add to my Calendar." Both paths in the
adapter were fictional. Worse,
`content.aspx?page_id=4001&club_id=285841` redirects an anonymous request to the
Join Us page — BRBC sells advance ride notice as a **paid member benefit**.
Scraping past that gate is not something to put on the shop's IP. Marked
`optional` so it cannot fail the build.

Carry this forward: BRBC was projected at ~70% of raw volume, so **the original
60-150 event estimate is now optimistic.** With pisgah-area-sorba also expected
to yield zero, **assume the first build looks thin.** That is the sources'
state, not a bug. BRBC's `max_per_week: 4` cap and `prefer_titles` list are
inert. BRBC's public events (WNC Flyer, Tour de Transylvania) register through
BikeReg/RunSignup and arrive anyway.

**NICA NC — domain right, timing matters.** `northcarolinamtb.org` confirmed
against NICA's own league directory. **NC is a spring league** — racing late
January to June, registration opens Nov 1. From July to November it
legitimately returns nothing, and a `probe.py` FAIL in that window is seasonal,
not broken. Same for pisgah-rage and ic-imagine.

---

## What is left, in priority order

**1. Deploy.** Still not doable from a Claude sandbox — the GitHub identity is
read-only and unbound to any repo. Two paths:

*Clicks (Matt, no terminal):* see `DEPLOY.md`, written for this. New **public**
repo `altar-events` on github.com -> "uploading an existing file", drag in the
unzipped contents -> Settings -> Secrets and variables -> Actions -> new secret
`ANTHROPIC_API_KEY` -> Settings -> Pages -> Source **GitHub Actions** -> Custom
domain `calendar.altar.bike` -> Actions tab -> Run workflow. `site/CNAME` is
already committed.

*Terminal (Walsh, ~10 min):* `./bootstrap.sh` from inside the folder. It
defaults to **public** visibility, because Pages will not serve from a private
repo without a paid plan — that is a hard stop at the end of the process, so do
not flip it back casually. It writes `site/CNAME`, registers
`calendar.altar.bike` on the Pages config, sets secrets and triggers the first
build. `DOMAIN= ./bootstrap.sh` opts out to the github.io address.

**Deploying is also how the remaining verification gets done.** GitHub Actions
runners have full egress, so the first build's step summary and
`site/build-report.json` answer everything `probe.py --check` would have —
against the real endpoints, on a schedule, forever. Prefer shipping over
hand-probing.

**2. DNS.** One CNAME at the registrar: name `calendar`, value
`GraveloMatt.github.io`. Then Settings -> Pages -> Enforce HTTPS once it
resolves. Only Matt can do this.

**3. `RWGPS_API_KEY` — highest-value remaining item.** Free and self-serve at
`ridewithgps.com/api/v1/doc`. Unlocks AoB's Thursday rides *and* is now the only
realistic route to BRBC's rides (they advertise 600+ club routes there). One
key, two sources, and together they are most of what a customer means by "group
rides." With BRBC blocked and PAS stale, this is the single biggest lever on
how full the calendar looks.

**4. Pisgah Area SORBA's VolunteerHub URL.** One click for a human, turns a
dead source into an exact `ics` feed. See the finding above.

**5. Optional polish.** Cloudflare Worker for the submission form (`worker.js`
is written; paste its URL into `ENDPOINT` in `site/submit.html`). Until then the
form falls back to a pre-filled email to `events@altar.bike` — Matt confirmed
2026-08-17 that this address reaches a real inbox.

**6. Candidate extra source:** `greattrailsnc.com` (Great State Trails
Coalition) runs The Events Calendar with iCal export and already indexes G5
workdays with dates and times. Cheap addition at aggregator trust. Not yet
added or verified.

### Settled 2026-08-17 — do not re-litigate

- `events@altar.bike` reaches a real inbox. Confirmed by Matt.
- Matt has an Anthropic API key ready to paste into GitHub's secrets UI.
- **Sarah Cearley has signed off on the elevation strip.** It ships as built.
- BikeReg filter params — resolved above, no probe needed.
- Blue Ridge Heritage REST route — confirmed serving, no probe needed.

---

## Conventions to keep

- Adapter ladder is exact-feed first, `llm` last. Do not reach for `llm` when a
  real feed exists.
- Anything unverified says so in `sources.yml`, with a date.
- Real captured data beats invented fixtures in tests — see the BikeReg row,
  the G5 titles, and especially the RunSignup address block, where an invented
  fixture hid a real bug for a whole revision.
- Prefer self-verifying designs over confident guesses when an API can fail
  silently. The BikeReg filter ladder is the pattern: never trust a param name,
  check that the response actually contains what you asked for.
- When an API filters server-side, verify it is honoured before trusting it
  (RunSignup yes, BikeReg no) — and record which.
- Brand voice, colour and type for anything customer-facing live in the
  `altar-brand` skill. The calendar page and submission form are customer-facing.
