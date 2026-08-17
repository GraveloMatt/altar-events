# Altar Cycles — WNC cycling calendar

**Handoff brief. Read this first.** Third revision. The previous two each
carried one wrong assumption about the environment that cost most of a
session; see "Environment reality" below and **verify, do not trust**.

Project: aggregate every cycling event within 75 miles of the shop into one
calendar, published at `calendar.altar.bike`, embedded on altar.bike, and
subscribable as `.ics`.

**LIVE as of 2026-08-17.** https://calendar.altar.bike — repo
`GraveloMatt/altar-events` (public), GitHub Pages via Actions, HTTPS enforced,
DNS CNAME `calendar` -> `gravelomatt.github.io` at Squarespace. Rebuilds every
morning at 05:17 ET. Currently publishing 64 events.

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
  `/user/repos`, `POST /user/repos`, `/user/installations` and every
  `repos/GraveloMatt/*` path return 403 *"sessions are bound to their
  configured repositories"*, and `git` auth fails with *"Password
  authentication is not supported"*. Creating the repo did NOT unlock it — the
  binding is fixed when the session starts. **You cannot push from the shell.**
- **BUT: drive Chrome instead.** This is the lesson of the deploy session.
  Matt's browser is reachable through the `mcp__claude-in-chrome__*` tools and
  is signed in as GraveloMatt, so the entire GitHub UI is available — the repo,
  the uploads, Pages settings, Actions, re-runs. "No API access" is NOT "no
  access"; check the browser before writing anyone a manual click-guide.
  Practical notes for that path:
    * `file_upload` needs files under the session working directory, and it
      does NOT preserve folder structure — but `/upload/main/<any/new/path>`
      works even when the directory does not exist yet, so upload one
      directory at a time and let the URL place them.
    * Take a fresh screenshot before clicking a button and use ITS coordinates.
      Stale `ref_` ids from an earlier page silently no-op — three commits were
      lost that way before it was noticed.
    * Do not type into GitHub pages with `computer:type` without focusing a
      real field first; loose keystrokes hit GitHub's single-key shortcuts and
      navigate you to Copilot. Use `form_input` with a ref.
    * Never enter the API key. Fill the secret's *name*, then hand over.
- `gh` is not installed. There is no `ANTHROPIC_API_KEY` in the environment.
- **Do not ask Matt to paste a token or API key into chat.** Direct him to
  enter secrets himself in GitHub's UI.

---

## State of the code

Two suites, both green (2026-08-17). Run them after any change:

```bash
python test_pipeline.py      # 117 checks
python test_integration.py   # 56 checks
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

## Source inventory — 15 sources

| id | adapter | trust | status |
|---|---|---|---|
| asheville-on-bikes | llm | 80 | needs `ANTHROPIC_API_KEY` |
| asheville-on-bikes-rwgps | ridewithgps | 75 | **DEAD END** — see below, optional |
| darc | llm | 80 | needs `ANTHROPIC_API_KEY` |
| blue-ridge-dirt-skrrts | llm | 80 | **ADDED 2026-08-17**; best group-ride source |
| velosports-ring-of-fire | llm | 80 | **ADDED**; seasonal May-Jun, optional |
| blue-ridge-bicycle-club | clubexpress | 80 | **BLOCKED**, optional |
| pisgah-area-sorba | squarespace | 80 | **STALE**, optional; 1 event via llm |
| g5-trail-collective | wix | 80 | **VERIFIED working** |
| nica-nc | jsonld | 80 | **URL corrected**; empty Jul-Nov by design |
| pisgah-rage | llm | 50 | seasonal, mirrors NICA |
| ic-imagine-cycling | llm | 50 | seasonal, optional |
| bikereg | bikereg | 60 | **VERIFIED**; 31 events |
| runsignup | runsignup | 60 | **title-only keywords**; 1 event, by design |
| uci | llm | 60 | cosmetic, optional |
| blue-ridge-heritage | tribe | 40 | route serves; **keywords tightened** |

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

**RunSignup flooded the calendar with running races — and the date fix caused
it.** Getting the format right took this source 0 -> 23 events, and all 23 were
running: Black Mountain Turkey Trot, HalloWine 5k, Color Me Mutt 5K, Blue Ridge
Relay, Dollywood's Light The Way 5k, two sprint triathlons. Two causes, both
worth remembering because they generalise:
  1. `require_keywords` matched the **description** as well as the title, and
     running-race blurbs mention bikes constantly — "bike valet", "no bikes on
     course", "packet pickup at the bike shop". New per-source flag
     `require_in_title` forces the keyword into the title. It is opt-in;
     description matching is still right for orgs that write vague titles.
  2. The bare keyword `mountain` matched **place names** — "GTC Paris Mountain
     Road Race" (a running club), "Black Mountain Turkey Trot". Removed; "mtb"
     and "bike" already cover mountain biking since "mountain bike" contains
     "bike".
Result: 23 -> 1. The survivor, "1000 Mile BikeWalk for Prevention", has a real
ride in it and Matt chose to keep it, along with CRAFTED (a handbuilt bicycle
show) and the Dirt Skrrts self-defense class (club programming). Do not "clean
up" those three.

**Blue Ridge Dirt Skrrts — added, and the best group-ride source available.**
Women's/non-binary MTB nonprofit. Squarespace; `?format=json` is
robots-disallowed exactly like Pisgah Area SORBA, so the llm rung reads
/schedule, which renders cleanly server-side with date, times and venue.
Monthly group rides confirmed through December. Took Group Rides from 15 to 19
on the live site — the category that was thinnest with BRBC walled off.

**Ring of Fire — found, but seasonal and already over for 2026.** Track racing
at the Carrier Park velodrome (the "Mellowdrome", 500 Amboy Rd), run by
VeloSports Racing with Cane Creek. The 2026 series ran **6 May - 24 June, every
Wednesday, 7 races, first race 5:30pm**. Entry is "MUST REGISTER ON BikeReg -
NO ONSITE REGISTRATION", so 2027 should arrive through the bikereg source
anyway; this entry catches the series listing earlier and names the venue.
Expect zero from July until the 2027 season is announced.

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

Deploy is DONE. What follows is what would most improve the calendar.

**1. RWGPS — CLOSED 2026-08-17. Do not chase this again.** Three revisions of
this brief called an RWGPS key "by far the highest-value remaining item". That
was wrong, and it was never verified. Matt got a key, and here is what the live
sites actually show:

* **Asheville on Bikes** (`/clubs/1802-asheville-on-bikes/events`) — the Events
  tab's most recent entry is **4 May 2024**. Two years stale. No upcoming
  events at all.
* **Blue Ridge Bicycle Club** — they are an *organization*, not a club:
  `ridewithgps.com/organizations/120-blue-ridge-bicycle-club`. Their Events tab
  says **"No events in August"** with 3 unscheduled events.

Both clubs use Ride with GPS as a **route library**, not an event calendar. The
"600+ BRBC routes" figure that made this look valuable counts *routes* — GPX
files with no dates — which are not calendar events and never will be. The
weekly rides genuinely are not published anywhere machine-readable; they live
on Facebook, in email, and behind BRBC's member wall.

The adapter still fails with a 404 because `/api/v1/clubs/{id}/events.json` was
invented and the v1 docs document no club or event endpoints at all (only
`/api/v1/routes.json`). Fixing the path is NOT worth doing: the credentials are
accepted — the error moved from "needs RWGPS_API_KEY" to a real 404 once the
secrets were wired into build.yml — so a corrected path would just return an
empty list more politely. The source stays `optional` and inert.

**Keep the secrets in place.** They cost nothing, they are correctly wired, and
if either club ever starts scheduling events properly the only work left is the
endpoint path.

**2. Pisgah Area SORBA's VolunteerHub URL.** One click for a human: open their
events page, click any "click Here!" button, read the domain off the address
bar. VolunteerHub exposes iCal, which turns a dead source into an exact `ics`
feed and brings back their dig days.

**2. Recurring events — DONE 2026-08-17.** `normalize.expand_recurrence`
turns one row carrying `repeat: weekly|biweekly|monthly` (+ optional
`repeat_until`) into the dated occurrences it means. Wired into `prepare()`, so
it works for `data/manual.yml`, the submission form and the issue template
alike. With no `repeat_until` it publishes `RECUR_DEFAULT_COUNT` (12) and
stops, on purpose — a weekly ride over the 400-day horizon would be ~57 entries
and would swamp everything else, and standing rides go stale faster than anyone
updates them. Monthly walks calendar months, not 28 days, and clamps (31 Aug ->
30 Sep). Every case is pinned in `test_pipeline.py`.

**2c. Submissions now actually gate on approval.** The code always filtered on
`labels=approved,event-submission`, but **neither label existed in the repo**,
so the gate was untestable and the issue template's `labels:` line was a no-op.
Both labels created 2026-08-17. Nothing from the public form reaches the
calendar until Matt adds `approved`; removing it takes the event down again.

**2d. Multi-day events publish once per day.** Blue Ridge Heritage's craft
exhibition appeared ELEVEN times before the keyword fix removed it entirely.
The underlying behaviour is still there and will bite any future source whose
feed emits one entry per day of a multi-day event. Nothing currently in
`sources.yml` triggers it, so this is latent, not urgent — but if a stage race
or a festival ever shows up eight days running, this is why. Fix would live in
`normalize.dedupe`: collapse same-title consecutive-day runs from one source
into a single event with an end date.

**4. `ic-imagine-cycling` connection failures.** `www.icimaginecycling.org`
refused connections on every live build ("Max retries exceeded"). It is marked
`optional` so it cannot fail the build, and it is a seasonal youth source that
would be empty now anyway. Check again in November; if the domain is gone,
delete the source rather than leaving a permanent `down`.

**5. Optional polish.** Cloudflare Worker for the submission form (`worker.js`
is written; paste its URL into `ENDPOINT` in `site/submit.html`). Until then the
form falls back to a pre-filled email to `events@altar.bike` — Matt confirmed
2026-08-17 that this address reaches a real inbox.

**6. Candidate extra source:** `greattrailsnc.com` (Great State Trails
Coalition) runs The Events Calendar with iCal export and already indexes G5
workdays. Cheap addition at aggregator trust. **Apply the Blue Ridge Heritage
lesson if you add it:** do not put bare `trail` in its `require_keywords`.

**7. Title hygiene.** Some promoter-supplied titles arrive shouting, e.g.
"SOLD OUT !!! ———12TH ANNUAL DANCING BEAR BIKE BASH RETURNS ON SEPTEMBER 19TH,
2026". Cosmetic, customer-facing, and the calendar page is governed by the
`altar-brand` skill. Worth a normalisation pass on titles if it grates.

### Settled 2026-08-17 — do not re-litigate

- `events@altar.bike` reaches a real inbox. Confirmed by Matt.
- **Sarah Cearley has signed off on the elevation strip.** It is live.
- BikeReg filter params — resolved, no probe needed.
- Blue Ridge Heritage REST route — confirmed serving.
- RunSignup date format — ISO. BikeReg's is MM/DD/YYYY. Deliberately different.
- DNS, Pages, HTTPS, the API key secret — all done.

---

### What the first live builds taught us

Three bugs reached production that no offline test caught, and all three were
**silent** — they looked like quiet sources, not errors:

1. **RunSignup sent `MM/DD/YYYY`.** The API answered `param_datatype_mismatch`
   and the adapter reported "returned 0 events". Fixing the format took the
   source from 0 to 36 events and the calendar from 81 to 117.
2. **`nica-nc` pointed at `/schedule/`, which 404s.** The real page is
   `/event-weekends`.
3. **`trail` in Blue Ridge Heritage's `require_keywords`** matched "Blue Ridge
   Craft Trails" and "Trails Less Traveled", putting 13 craft-and-hiking events
   on the shop's public calendar, classified `mtb`.

The general lesson, worth keeping: **a source reporting zero is not evidence it
is working.** Run #1 also failed on `git push` because a web-UI edit landed
mid-build; the workflow now rebases and retries three times. And GitHub Pages
itself returned 503 twice during an active githubstatus.com incident — when
`deploy` fails but `build` is green, check status before touching code.

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
- **A source reporting zero events is not evidence it is working.** Three
  separate silent failures shipped to the live site on day one, each of which
  read as "quiet season". When a source returns nothing, check the request, not
  just the response.
- **Read the live site after a change, not just the build log.** The craft-fair
  bug was invisible in the summary — "blue-ridge-heritage — ok (32 events)" —
  and obvious the moment anyone looked at the page.
