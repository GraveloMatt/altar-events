# Running the calendar

The calendar is live at **https://calendar.altar.bike**. It rebuilds itself
every morning at 5:17am, before the shop opens. Nobody has to do anything to
keep it running.

This is the page to come back to when you want to change something or when
something looks wrong. Written for clicking, not for a terminal.

*(Deployment is done. If you're looking for the original setup steps, they're
in the repo's git history — this file replaced them on 2026-08-17.)*

---

## The links

| what | where |
|---|---|
| The calendar | https://calendar.altar.bike |
| Subscribe — everything | https://calendar.altar.bike/events.ics |
| Subscribe — races only | https://calendar.altar.bike/races.ics |
| Subscribe — dig days only | https://calendar.altar.bike/trail-work.ics |
| Submit an event | https://calendar.altar.bike/submit.html |
| The code | https://github.com/GraveloMatt/altar-events |

The `.ics` links work in Google Calendar, Apple Calendar and Outlook — "add
calendar by URL." That's what Walsh wants for the altar.bike embed.

---

## Adding an event by hand

Use this for Altar's own rides, demo days, clinics and film nights, for
anything a promoter texts you before it's online, and to correct a scraped
event that came through wrong.

1. Go to **https://github.com/GraveloMatt/altar-events/blob/main/data/manual.yml**
2. Click the **pencil icon** (top right of the file).
3. Add your event following the commented example at the bottom of the file.
   Only `title` and `start` are required.
4. Click **Commit changes** twice.

That's it — saving triggers a rebuild, and the calendar updates in about three
minutes.

Hand-entered events beat everything. If a scraped event has the wrong time or a
mangled title, add a correct one here with the **same title and date** and yours
wins.

Watch the indentation — YAML cares. If the build goes red after an edit, that's
almost always why.

---

## Checking on it

**https://github.com/GraveloMatt/altar-events/actions**

Each morning's run appears here. Click the top one and you get a summary like:

> **64 events published**
> - asheville-on-bikes — ok (21 events via llm)
> - bikereg — ok (31 events via bikereg)
> - blue-ridge-bicycle-club — down — …

A green tick means it worked. **Some sources always say `down`,** and that's
expected — see the next section. What matters is that the run is green and the
event count is in the right ballpark.

---

## Sources that are *supposed* to look broken

Don't chase these. They're understood and written down.

**Blue Ridge Bicycle Club — permanently blocked.** They sell advance ride notice
as a paid member benefit, so their calendar is behind a member wall. We're not
going to scrape past that. Their public events (WNC Flyer, Tour de
Transylvania) still arrive via BikeReg and RunSignup.

**Pisgah Area SORBA — stale page.** Their events page hasn't been updated since
February; they run signups through VolunteerHub. **If you can get me that
VolunteerHub link** it becomes a proper feed and their dig days come back. Open
their events page, click any "click Here!" button, and read the address bar.

**Ring of Fire — seasonal.** The velodrome series at Carrier Park runs
Wednesdays from early May to late June. It's empty the rest of the year, and
entry is BikeReg-only so it arrives through that source too.

**RunSignup — nearly always 0 or 1 event, on purpose.** It's a running-race
platform. It only publishes here when a race has an actual cycling word in its
title, because without that rule you get turkey trots and 5Ks.

**The three youth/NICA sources — seasonal.** North Carolina is a *spring*
league: racing runs late January to June, and registration opens November 1.
They're legitimately empty from July to November.

**Ride with GPS — a dead end, and I was wrong about it.** I told you this was
the biggest available improvement. It isn't. Your key is installed and working,
but I checked both clubs' actual Ride with GPS pages: Asheville on Bikes' last
event there was **May 2024**, and Blue Ridge Bicycle Club's events tab says "No
events in August". Both use Ride with GPS to store *routes* — GPX files with no
dates — not to schedule rides. There is nothing there to fetch. The secrets are
staying put in case that ever changes; they cost nothing.

---

## Where the weekly rides actually are

The recurring group rides — the AoB Thursday rides, BRBC's weekly calendar —
are not published anywhere a computer can read. They live on Facebook, in
email newsletters, and behind BRBC's member wall.

Two things that would genuinely help, both of them human:

**Ask the ride leaders to send you dates.** One email a season to AoB and the
Dirt Skrrts, then `data/manual.yml` (see above). Hand-entered events outrank
everything and it's about two minutes per ride series.

**Ask Pisgah Area SORBA whether they publish a feed.** We currently read their
VolunteerHub portal through an undocumented internal route, which works today
but could break without warning. A shop-to-club email is more durable than any
endpoint I can find.

---

## When something's wrong

**An event is wrong or missing.** Don't fight the scraper — add it to
`data/manual.yml` (see above). That's what it's for.

**The morning run is red.** Click into it and read the failed step.
- If it mentions YAML or a parse error, it's an edit to `manual.yml` or
  `sources.yml` — check the indentation.
- If `build` is green but `deploy` failed, that's usually GitHub, not us.
  Check **githubstatus.com**, then use **Re-run jobs → Re-run failed jobs**.
  This happened on launch day; it cleared on its own.

**The site won't load.** Check
https://github.com/GraveloMatt/altar-events/settings/pages still shows
`calendar.altar.bike` under Custom domain with a green "DNS check successful".
The Squarespace record it depends on is: type `CNAME`, host `calendar`, data
`gravelomatt.github.io`.

**Something non-cycling is on the calendar.** It got through a keyword filter.
Tell me which event and I'll tighten it. This has happened twice: a craft
exhibition that matched the word "trail", and a wave of running races whose
descriptions mentioned "bike valet". Both are fixed, and both are pinned as
tests so they can't come back.

Three borderline events are on there **deliberately** — CRAFTED (a handbuilt
bicycle show), the 1000 Mile BikeWalk (a charity ride), and the Dirt Skrrts
self-defense class (bike club programming). Not oversights.

**You want it rebuilt right now.** Actions tab → **Build events calendar** →
**Run workflow** → green **Run workflow** button. Takes about three minutes.
