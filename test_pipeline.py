"""Offline checks for the parts that don't need network. Run: python test_pipeline.py"""
import json
from datetime import datetime, timedelta

import normalize
import build

HOME = {"lat": 35.5951, "lng": -82.5515, "zip": "28801"}
R = 75
soon = lambda d: (datetime.now() + timedelta(days=d)).date().isoformat()

fails = []


def check(label, got, want):
    if got != want:
        fails.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL {label}: {got!r} != {want!r}")
    else:
        print(f"  ok   {label}")


print("\nclassification")
cases = [
    ("Butter Gap Trail Work Day", "trail-work"),
    ("Old Fort Fifty", "race"),
    ("Pisgah Monster Cross Race", "race"),
    ("Summer Cycle 2026", "festival"),
    ("Tuesday Night Community Ride", "group-ride"),
    ("NICA Race at Tryon International", "youth"),
    ("Smart Cycling Class", "clinic"),
    ("Trail Work Day before the Old Fort Fifty", "trail-work"),  # trail-work beats race
    ("Gravity Carolinas ENDURO RD 3", "race"),
    ("Bike Love Gala", "festival"),
]
for title, want in cases:
    check(title[:44], normalize.classify({"title": title, "description": ""}, "race")[0], want)

print("\ndiscipline")
for title, want in [("Dirt Diggler Gravel Grinder", "gravel"),
                    ("Pisgah Stage Race", "mtb"),
                    ("NC Grand Prix Cyclocross", "cx"),
                    ("Hilly Hellacious Hundred century", "road")]:
    check(title[:44], normalize.discipline({"title": title, "description": ""}), want)

print("\ngeofence")
check("Brevard by coords",
      normalize.in_region({"lat": 35.2334, "lng": -82.7343}, HOME, R), True)
check("Charlotte by coords (130mi)",
      normalize.in_region({"lat": 35.2271, "lng": -80.8431}, HOME, R), False)
check("Old Fort by city name",
      normalize.in_region({"city": "Old Fort", "state": "NC"}, HOME, R), True)
check("Wilmington by city name",
      normalize.in_region({"city": "Wilmington", "state": "NC"}, HOME, R), False)
check("no location at all -> trust the org",
      normalize.in_region({"title": "Shop ride"}, HOME, R), True)

# Regression, 2026-08-17. RunSignup returns no coordinates at all, but its
# zipcode+radius filter is honoured server-side (verified: radius=5 around
# 28801 returns only Asheville/Woodfin). Before the pre_geofenced flag these
# races fell through to the REGION_TOWNS name match and Statesville /
# Morristown events the server had already vouched for were silently dropped.
check("pre_geofenced beats an unknown town",
      normalize.in_region(
          {"city": "Statesville", "state": "NC", "pre_geofenced": True},
          HOME, R), True)
check("unknown town without the flag is still dropped",
      normalize.in_region({"city": "Statesville", "state": "NC"}, HOME, R), False)
check("real coordinates still overrule the flag",
      normalize.in_region(
          {"lat": 35.2271, "lng": -80.8431, "pre_geofenced": True}, HOME, R), False)

print("\ndedupe")
raw = [
    {"title": "Old Fort Fifty", "start": soon(30), "source_name": "BikeReg",
     "trust": 60, "url": "https://bikereg.com/old-fort-fifty", "city": "Old Fort"},
    {"title": "2026 Old Fort Fifty presented by Camp Grier", "start": soon(30),
     "source_name": "G5 Trail Collective", "trust": 80, "url": "https://g5.org/e",
     "description": "30.4 miles from Camp Grier."},
    {"title": "Dirt Diggler Gravel Grinder", "start": soon(45),
     "source_name": "DARC", "trust": 80, "url": "https://darc/x"},
]
for e in raw:
    e["uid"] = normalize.uid(e)
merged = normalize.dedupe(raw)
check("3 raw -> 2 unique", len(merged), 2)
winner = next(e for e in merged if "Old Fort" in e["title"])
check("higher-trust title wins", winner["source_name"], "G5 Trail Collective")
check("cross-credits the other source", winner["also_listed_by"], ["BikeReg"])
check("backfills missing city from loser", winner.get("city"), "Old Fort")

print("\nweekly cap (BRBC-style flood)")
flood = [{"title": f"Tuesday Ride {i}", "start": f"{soon(7 + i)}T18:00:00"}
         for i in range(14)]
flood.append({"title": "WNC Flyer", "start": f"{soon(9)}T08:00:00"})
capped = normalize.cap_weekly(flood, per_week=4, keep_matching=["flyer"])
check("flagged event always kept",
      any(e["title"] == "WNC Flyer" for e in capped), True)
check("cap actually bites", len(capped) < len(flood), True)
weeks = {}
for e in capped:
    if e["title"] == "WNC Flyer":
        continue
    w = datetime.fromisoformat(e["start"]).isocalendar()[:2]
    weeks[w] = weeks.get(w, 0) + 1
check("no week over 4", max(weeks.values()), 4)

print("\nprepare(): filters + enrichment")
src = {"id": "t", "name": "Test", "trust": 80, "default_category": "race",
       "org_url": "https://x", "drop_if_titled": ["practice"],
       "require_keywords": ["bike", "gravel", "trail"]}
prepared = normalize.prepare([
    {"title": "Team practice", "start": soon(5), "description": "bike"},   # blocked
    {"title": "5K Turkey Trot", "start": soon(6), "description": "run"},   # no keyword
    {"title": "Gravel Grinder", "start": soon(7), "city": "Brevard"},      # keep
    {"title": "Last year's race", "start": soon(-40), "description": "bike"},  # past
    {"title": "Too far out", "start": soon(900), "description": "bike"},   # horizon
], src, HOME, R)
check("only the valid one survives", [e["title"] for e in prepared], ["Gravel Grinder"])
check("uid assigned", bool(prepared[0]["uid"]), True)
check("category assigned", prepared[0]["category"], "race")
check("discipline assigned", prepared[0]["discipline"], "gravel")

print("\nics output")
ics = build.to_ics(prepared, "Test Cal", "desc").decode()
for token in ("BEGIN:VCALENDAR", "X-WR-CALNAME:Test Cal", "BEGIN:VEVENT",
              "SUMMARY:Gravel Grinder", "UID:", "@altar.bike", "END:VCALENDAR"):
    check(f"contains {token}", token in ics, True)
check("folded to <=75 octets",
      max(len(l.encode()) for l in ics.split("\r\n")) <= 75, True)

print("\nissue-form parsing")
parsed = build.parse_issue_body("""### Event name

Bent Creek Dig Day

### Date

2026-09-12

### Start time

09:00

### City

Asheville

### Link

https://example.org/dig

### Details

_No response_
""")
check("title", parsed["title"], "Bent Creek Dig Day")
check("date+time combined", parsed["start"], "2026-09-12 09:00")
check("city", parsed["city"], "Asheville")
check("skips _No response_", "description" in parsed, False)

print("\nbikereg REST parsing")
import adapters

# Trimmed verbatim from https://www.bikereg.com/api/search on 2026-08-17.
# Mars Hill is ~26 mi from the shop, so this row must survive the geofence.
BIKEREG_ROW = {
    "EventName": "Mars Hill Cycling Camp Road and Gravel Bike",
    "EventDate": "/Date(1781496000000-0400)/",
    "EventEndDate": "/Date(1781928000000-0400)/",
    "EventAddress": "155 Townhouse Drive Mars Hill, NC 28754",
    "EventCity": "Mars Hill",
    "EventState": "NC",
    "Latitude": 35.8236799617435,
    "Longitude": -82.5514130554504,
    "EventNotes": "<p>Registration Process:&nbsp;<strong>Please Read!</strong></p>",
    "EventTypes": ["Cycling Camp", "Gravel"],
    "EventUrl": "http://www.BikeReg.com/mh-road",
    "EventPermalink": "http://www.BikeReg.com/73384",
    "EventLogo": None,
    "CoverPhoto": None,
}

parsed = adapters._bikereg_event(BIKEREG_ROW)
check("ms-date start", parsed["start"][:10], "2026-06-15")
check("ms-date end", parsed["end"][:10], "2026-06-20")
check("city", parsed["city"], "Mars Hill")
check("notes html stripped", parsed["description"], "Registration Process: Please Read!")
check("discipline hint from EventTypes", parsed["discipline_hint"], "Cycling Camp")
check("within geofence", adapters._within(parsed, HOME, R), True)

# Live data contains rows where the promoter pasted a full URL into the
# BikeReg-relative field, producing this. Permalink must win.
check("malformed EventUrl rejected",
      adapters._bikereg_event({**BIKEREG_ROW,
                               "EventUrl": "http://www.BikeReg.comhttps://roadnats.usacycling.org/pro-road"})["url"],
      "http://www.BikeReg.com/73384")
check("good EventUrl kept", parsed["url"], "http://www.BikeReg.com/mh-road")

# A Wyoming race in the same payload must not pass the geofence.
check("far event excluded",
      adapters._within(adapters._bikereg_event(
          {**BIKEREG_ROW, "Latitude": 43.783612, "Longitude": -110.9464081}), HOME, R),
      False)
check("missing coords excluded",
      adapters._within(adapters._bikereg_event(
          {**BIKEREG_ROW, "Latitude": None, "Longitude": None}), HOME, R),
      False)
check("ms_date passes through epoch millis", adapters.ms_date(1781496000000)[:10], "2026-06-15")
check("ms_date survives junk", adapters.ms_date("not a date"), None)

# Pins the params confirmed against BikeReg's own docs on 2026-08-17. The
# first rung must be the exact-radius `loc`+`distance` shape, because the
# region names are coarse and `region=North Carolina` is silently IGNORED —
# it returns the national list, which looks like success. If someone
# "simplifies" this ladder back to a region name, these fail loudly.
shapes = adapters._bikereg_filters(HOME, 100)
first = shapes[0]
check("loc is the first rung", "loc" in first, True)
check("loc is lat|lng", first["loc"], "35.5951|-82.5515")
check("distance carries the radius", first["distance"], 100)
check("every dated rung sends startDate", all("startDate" in s for s in shapes[:4]), True)
check("dates are MM/DD/YYYY", len(first["startDate"].split("/")[-1]), 4)
check("no rung uses the ignored region=North Carolina",
      any(s.get("region") == "North Carolina" for s in shapes), False)
check("region rung uses Southeast (the region NC is in)",
      any(s.get("region") == "Southeast" for s in shapes), True)
check("no rung uses a hyphenated region name",
      any("-" in str(s.get("region", "")) for s in shapes), False)

print("\nlong spans (seasons and series posing as one event)")
# All four captured from the live calendar on 2026-08-17. Building the month
# grid is what exposed them: in an agenda list a 180-day event is one harmless
# row, in a grid it paints every cell it covers.
_cx = normalize.split_long_span(
    {"title": "2026 Tuesday Night Cyclocross Training Series",
     "start": "2026-09-15T18:00:00", "end": "2026-09-29T18:00:00"})
check("weekday named in title -> weekly occurrences", len(_cx), 3)
check("occurrences land on the named weekday",
      [datetime.fromisoformat(e["start"]).strftime("%a") for e in _cx],
      ["Tue", "Tue", "Tue"])
check("split occurrences drop the long end", "end" in _cx[0], False)

for title, start, end in [
        ("Pisgah Rage Regular Season", "2026-12-01", "2027-05-30"),
        ("Bear's Smokehouse BBQ Community Rides", "2027-05-01", "2027-08-31"),
        ("Pisgah Rage Pre-season", "2026-10-15", "2026-12-31")]:
    got = normalize.split_long_span({"title": title, "start": start, "end": end})
    check("no cadence stated -> one entry: " + title[:26], len(got), 1)
    check("  and the true range is recorded",
          "Runs" in (got[0].get("description") or ""), True)
    check("  and the long end is dropped", "end" in got[0], False)

# Genuine weekenders must be left completely alone.
for title, start, end, days in [
        ("Rocky Knob Mountaineer Showdown", "2026-08-29", "2026-08-30", 1),
        ("Fall Trail Weekend", "2026-11-06", "2026-11-08", 2)]:
    got = normalize.split_long_span({"title": title, "start": start, "end": end})
    check("weekender untouched: " + title[:28], len(got) == 1 and got[0].get("end") == end, True)

check("junk dates cannot crash the split",
      len(normalize.split_long_span(
          {"title": "Tuesday thing", "start": "nope", "end": "also nope"})), 1)
check("an event with no end is passed straight through",
      len(normalize.split_long_span({"title": "X", "start": "2026-09-01"})), 1)

print("\ntitle tidying")
# The first case is verbatim from calendar.altar.bike on launch day.
for messy, want in [
    ("SOLD OUT !!! ———12TH ANNUAL DANCING BEAR BIKE BASH RETURNS ON SEPTEMBER 19TH, 2026",
     "12th Annual Dancing Bear Bike Bash Returns on September 19th, 2026"),
    ("NEW! Old Fort Fifty", "Old Fort Fifty"),
    ("REGISTER NOW - Pisgah Stage Race", "Pisgah Stage Race"),
    # Acronyms and normal titles must survive untouched.
    ("NCCX Race #3", "NCCX Race #3"),
    ("WNC Flyer", "WNC Flyer"),
    ("UCI World Cup", "UCI World Cup"),
    ("Old Fort Fifty", "Old Fort Fifty"),
    ("Gravel Roll — The Holler in Walhalla", "Gravel Roll — The Holler in Walhalla"),
]:
    check(messy[:44], normalize.tidy_title(messy), want)
check("tidying happens before the uid is taken",
      normalize.prepare(
          [{"title": "NEW! Bent Creek Dig Day", "start": soon(9),
            "city": "Asheville"}],
          {"id": "t", "name": "T", "trust": 80, "default_category": "trail-work",
           "org_url": "https://x"}, HOME, R)[0]["title"],
      "Bent Creek Dig Day")

print("\nlisted_by — crediting somebody else's ride")
# Everything in data/manual.yml is stamped "Altar Cycles". On 2026-08-24 the
# Gravelo Wednesday ride published as "Listed by Altar Cycles", which reads as
# though Altar hosts it — a rider could turn up at the wrong shop.
_ALTAR = {"id": "altar", "name": "Altar Cycles", "trust": 100,
          "default_category": "race", "org_url": "https://altar.bike",
          "hand_entered": True}
_theirs = normalize.prepare(
    [{"title": "Gravelo Wednesday Shop Ride", "start": soon(2),
      "listed_by": "Gravelo Workshop", "city": "Asheville", "state": "NC"}],
    _ALTAR, HOME, R)
check("someone else's ride credits them", _theirs[0]["source_name"], "Gravelo Workshop")
check("the override is consumed, not published as a stray field",
      "listed_by" in _theirs[0], False)
check("but it is still OUR entry, at our trust", _theirs[0]["trust"], 100)
check("and still attributed to the altar source id", _theirs[0]["source"], "altar")

_ours = normalize.prepare(
    [{"title": "Altar Shop Ride — Bent Creek", "start": soon(3),
      "city": "Asheville", "state": "NC"}], _ALTAR, HOME, R)
check("Altar's own ride still credits Altar", _ours[0]["source_name"], "Altar Cycles")

# A scraped source must never be able to rewrite its own credit line.
_scraped = normalize.prepare(
    [{"title": "Some Race", "start": soon(4), "listed_by": "Totally Legit Org",
      "city": "Asheville", "state": "NC"}],
    {"id": "bikereg", "name": "BikeReg", "trust": 60, "default_category": "race",
     "org_url": "https://www.bikereg.com/"}, HOME, R)
check("a scraped feed cannot forge a credit line it did not earn",
      _scraped[0]["source_name"], "BikeReg")
check("and the forged field never reaches the published record",
      "listed_by" in _scraped[0], False)
# The credit line is the one field that says WHO VOUCHES for an event. The llm
# adapter reads whatever prose a site serves, so a page containing
# "listed_by: <anyone>" must not be able to put that name under an event.
check("only hand-entered sources may set it",
      normalize.prepare(
          [{"title": "Some Race", "start": soon(5), "listed_by": "Nope",
            "city": "Asheville", "state": "NC"}],
          {**_ALTAR, "hand_entered": False}, HOME, R)[0]["source_name"],
      "Altar Cycles")


print("\nhand-entered YAML dates (regression: manual.yml would crash the build)")
# PyYAML parses an unquoted `start: 2026-09-04T18:00:00` into a datetime
# OBJECT, not a string, and every downstream check slices start[:10]. The
# worked example in data/manual.yml's own comments is unquoted, so the first
# standing ride anyone added would have died with "'datetime.datetime' object
# is not subscriptable". The file sat empty from day one, which is the only
# reason it never fired — found 2026-08-24 adding the Gravelo Wednesday ride.
import yaml as _yaml
_parsed = _yaml.safe_load("""
events:
  - title: Gravelo Wednesday Shop Ride
    start: 2026-08-26T18:00:00
    repeat: weekly
    repeat_until: 2026-10-28
    city: Asheville
    state: NC
""")["events"]
check("YAML really does hand back a datetime, not a string",
      isinstance(_parsed[0]["start"], datetime), True)
_manual = {"id": "altar", "name": "Altar Cycles", "trust": 100,
           "default_category": "race", "org_url": "https://altar.bike"}
_rides = normalize.prepare(_parsed, _manual, HOME, R)
check("a hand-entered standing ride survives", len(_rides), 10)
check("every occurrence is a Wednesday",
      {datetime.fromisoformat(e["start"]).weekday() for e in _rides}, {2})
check("repeat_until is respected to the day",
      (_rides[0]["start"][:10], _rides[-1]["start"][:10]),
      ("2026-08-26", "2026-10-28"))
check("time of day is kept", _rides[3]["start"][11:], "18:00:00")
check("each occurrence gets its own uid",
      len({e["uid"] for e in _rides}), 10)
check("the shop's own entry outranks anything scraped", _rides[0]["trust"], 100)
# The coercion itself, directly.
check("a bare date coerces too", normalize.as_iso(datetime(2026, 9, 4).date()),
      "2026-09-04")
check("a string is left alone", normalize.as_iso("2026-09-04"), "2026-09-04")
check("None is left alone", normalize.as_iso(None), None)


print("\nrecurring events")
_wk = normalize.expand_recurrence(
    {"title": "Altar Shop Ride", "start": soon(5) + "T18:00:00", "repeat": "weekly"})
check("weekly with no end date is capped", len(_wk), normalize.RECUR_DEFAULT_COUNT)
check("first occurrence keeps the exact original start",
      _wk[0]["start"], soon(5) + "T18:00:00")
check("occurrences are 7 days apart",
      (datetime.fromisoformat(_wk[1]["start"])
       - datetime.fromisoformat(_wk[0]["start"])).days, 7)
check("time of day is preserved", _wk[-1]["start"][-8:], "18:00:00")
check("the repeat key is consumed, not published", "repeat" in _wk[0], False)
check("occurrences are tagged recurring", _wk[0]["recurring"], "weekly")

check("repeat_until bounds the series",
      len(normalize.expand_recurrence(
          {"title": "X", "start": soon(5) + "T18:00:00", "repeat": "weekly",
           "repeat_until": soon(33)})), 5)

# Monthly must be calendar months, not 28 days — a ride on the 31st has to
# land on the 30th in September, not drift backwards every month.
check("monthly clamps to short months",
      [e["start"][:10] for e in normalize.expand_recurrence(
          {"title": "X", "start": "2026-08-31T10:00:00", "repeat": "monthly",
           "repeat_until": "2026-12-31"})],
      ["2026-08-31", "2026-09-30", "2026-10-31", "2026-11-30", "2026-12-31"])

check("each occurrence keeps the original duration",
      all(e["end"][-8:] == "12:00:00" for e in normalize.expand_recurrence(
          {"title": "X", "start": soon(5) + "T09:00:00",
           "end": soon(5) + "T12:00:00", "repeat": "weekly",
           "repeat_until": soon(20)})), True)

check("a one-off is returned untouched",
      len(normalize.expand_recurrence({"title": "X", "start": soon(5)})), 1)
check("an unrecognised repeat value is treated as a one-off",
      len(normalize.expand_recurrence(
          {"title": "X", "start": soon(5), "repeat": "whenever"})), 1)
check("a junk start date cannot crash the expansion",
      len(normalize.expand_recurrence(
          {"title": "X", "start": "not a date", "repeat": "weekly"})), 1)
check("prepare() expands recurrence end to end",
      len(normalize.prepare(
          [{"title": "Altar Shop Ride", "start": soon(5) + "T18:00:00",
            "repeat": "weekly", "city": "Asheville"}],
          {"id": "altar", "name": "Altar", "trust": 100,
           "default_category": "group-ride", "org_url": "https://altar.bike"},
          HOME, R)), normalize.RECUR_DEFAULT_COUNT)

print("\nsubmitter's category answer is used, not discarded")
check("category_hint fills in when no keyword matches",
      normalize.classify({"title": "Thursday Evening Spin", "description": "",
                          "category_hint": "group-ride"}, "race")[0],
      "group-ride")
check("keyword rules still outrank the submitter's hint",
      normalize.classify({"title": "Old Fort Fifty Race", "description": "",
                          "category_hint": "clinic"}, "festival")[0] == "clinic",
      False)
check("a junk hint falls through to the default",
      normalize.classify({"title": "Something", "description": "",
                          "category_hint": "banana"}, "festival")[0], "festival")

print("\nrunsignup filter (regression: 23 running races reached the live site)")
# Every one of these published on calendar.altar.bike on 2026-08-17 after the
# date-format fix took this source from 0 events to 23. Two causes: keywords
# were matched against the DESCRIPTION too (running races mention "bike valet"
# and "no bikes on course"), and the bare keyword `mountain` matched place
# names like Paris Mountain and Black Mountain. Captured verbatim.
RSU = {
    "id": "runsignup", "name": "RunSignup", "trust": 60,
    "default_category": "race", "org_url": "https://runsignup.com/",
    "require_in_title": True,
    "require_keywords": ["bike", "bicycle", "cycl", "mtb", "gravel",
                         "gran fondo", "criterium", "cyclocross", "enduro",
                         "downhill", "fondo", "pedal"],
    "drop_if_titled": ["5k", "10k", "half marathon", "marathon", "turkey trot",
                       "fun run", "color run", "walk/run", "run/hike", "relay",
                       "triathlon", "duathlon", "tri charlotte", "jingle",
                       "hallowine"],
}
# A running-race description that name-drops bikes — the exact shape that
# defeated the old title-or-description match.
RUNNY = "Join us! Bike valet available. Packet pickup at the bike shop."
for title in ["Asheville Craft Beer Half Marathon & 10K/5K",
              "Black Mountain Turkey Trot 5k",
              "GTC Paris Mountain Road Race",
              "GTC Paris Mountain Trails 16K",
              "Lakeside Double Sprint Triathlon",
              "2nd Annual TALI Sadlers Creek Off-Road Duathlon",
              "Color Me Mutt 5K & 1 Mile Color Walk/Run",
              "Blue Ridge Relay",
              "Lake Summit 10 Mile Run/Hike",
              "HalloWine 5k",
              "Cades Cove Loop Lope"]:
    check("drops " + title[:40],
          bool(normalize.prepare(
              [{"title": title, "start": soon(30), "description": RUNNY,
                "pre_geofenced": True}], RSU, HOME, 100)), False)
# ...while genuine cycling still gets through on a title keyword.
for title in ["Old Fort Fifty Gravel Grinder", "Asheville Criterium",
              "Gran Fondo Asheville", "Pisgah MTB Stage Race"]:
    check("keeps " + title[:40],
          bool(normalize.prepare(
              [{"title": title, "start": soon(30), "description": "",
                "pre_geofenced": True}], RSU, HOME, 100)), True)
# The flag must be opt-in: without it, a description match still counts.
check("require_in_title is opt-in, not the default",
      bool(normalize.prepare(
          [{"title": "Autumn Classic", "start": soon(30),
            "description": "A gravel race.", "pre_geofenced": True}],
          {**RSU, "require_in_title": False, "drop_if_titled": []},
          HOME, 100)), True)

print("\nblue-ridge-heritage keyword filter (regression from the live site)")
# These titles came off calendar.altar.bike on launch day, where 13 non-cycling
# events had published. Cause: `trail` was in require_keywords, and this feed is
# a CULTURAL heritage calendar — "Blue Ridge Craft Trails" is a craft
# exhibition, "Trails Less Traveled" is a hike. `trail` stays safe on G5 and
# Pisgah Area SORBA, which are trail-work orgs; it is not safe here.
BRH = {
    "id": "blue-ridge-heritage", "name": "Blue Ridge National Heritage Area",
    "trust": 40, "default_category": "festival",
    "org_url": "https://www.blueridgeheritage.com/",
    "require_keywords": ["bike", "bicycle", "cycl", "mtb", "gravel", "pedal", "fondo"],
    "drop_if_titled": ["craft", "studio tour", "basket", "quilt", "pottery",
                       "weaving", "gallery", "exhibition", "hike", "hiking",
                       "paddle", "birding", "storytelling", "heritage trail"],
}
for title, keep in [
    ("Connecting to Place II: Blue Ridge Craft Trails Invitational", False),
    ("Trails Less Traveled: Charlies Bunion", False),
    ("Weaving our Heritage: Cherokee Baskets", False),
    ("Come To Leicester Annual Studio Tour", False),
    ("Blue Ridge Bike Fest", True),
    ("Gravel Grinder Fundraiser", True),
]:
    got = normalize.prepare(
        [{"title": title, "start": soon(20), "description": ""}],
        BRH, HOME, R)
    check(("keeps " if keep else "drops ") + title[:38], bool(got), keep)

print("\nclassification must not read URLs (regression: 12 rides became clinics)")
# Verbatim from the Gravelo Workshop feed, 2026-08-24. Every entry's
# description is a bare link, and the shop is called "Workshop", which is a
# CLINIC keyword. All twelve Saturday group rides published as clinics.
check("a link in the description does not decide the category",
      normalize.classify({"title": "9:30am Bakery Ride B Group Start at Gravelo",
                          "description": "More info: https://www.gravelo-workshop.com/bakery-ride"},
                         "group-ride"),
      ("group-ride", "default"))
check("a real clinic still classifies as one",
      normalize.classify({"title": "Women + Trail Skills Workshop",
                          "description": "Learn to corner."}, "race")[0], "clinic")
check("a bare www link is stripped too",
      normalize.classify({"title": "Sunday Social",
                          "description": "www.somewhere-workshop.com/join"},
                         "group-ride"), ("group-ride", "default"))


print("\ngravelo-workshop — shop hours must not become events")
# Real shapes from the live public Google Calendar, read 2026-08-24. The feed
# mixes the Saturday ride with all-day "SHOP CLOSED" opening-hours entries.
import yaml as _yaml
GRAV = [x for x in _yaml.safe_load(open("sources.yml"))["sources"]
        if x["id"] == "gravelo-workshop"][0]
_g = normalize.prepare([
    {"title": "SHOP CLOSED", "start": soon(7), "all_day": True, "venue": ""},
    {"title": "9:30am Bakery Ride B Group Start at Gravelo", "start": soon(5),
     "all_day": True,
     "venue": "Gravelo Workshop - Bicycles & Coffee, 793 Merrimon Ave, Asheville, NC 28804, USA"},
    # The reason `closed` alone is not in the drop list.
    {"title": "Closed Course Crit", "start": soon(26), "all_day": True,
     "venue": "Carrier Park, Asheville, NC"},
], GRAV, HOME, R)
_titles = [e["title"] for e in _g]
check("opening hours dropped", "SHOP CLOSED" not in _titles, True)
check("the Saturday ride survives",
      any("Bakery Ride" in t for t in _titles), True)
check("a real race with 'closed' in the title is not caught",
      any("Closed Course Crit" in t for t in _titles), True)
check("geofenced on the address, with no coordinates",
      all(e.get("lat") is None for e in _g), True)
check("categorised as a group ride",
      [e["category"] for e in _g if "Bakery" in e["title"]], ["group-ride"])


print("\nseasonal sources (a flag that is always red is wallpaper)")
from datetime import date as _date
NICA = {"id": "nica-nc", "season": [11, 6]}
check("in season in March",   build.in_season(NICA, _date(2027, 3, 1)), True)
check("in season in November", build.in_season(NICA, _date(2026, 11, 1)), True)
check("out of season in August", build.in_season(NICA, _date(2026, 8, 24)), False)
check("wrap-around handled at the year boundary",
      build.in_season(NICA, _date(2027, 1, 5)), True)
check("a summer season does not wrap",
      build.in_season({"season": [4, 7]}, _date(2026, 12, 1)), False)
check("no season declared means always in season", build.in_season({}), True)

check("an empty answer looks empty", build.looks_empty("llm: returned 0 events"), True)
check("missing JSON-LD on an empty page looks empty",
      build.looks_empty("jsonld: https://x: no schema.org Event blocks"), True)
# The distinction that keeps this honest: a source that cannot be reached is
# broken in July exactly as much as in March.
check("a dead domain does NOT look empty",
      build.looks_empty("llm: https://x: Max retries exceeded, NameResolutionError"), False)
check("a timeout does NOT look empty",
      build.looks_empty("llm: https://api.anthropic.com/v1/messages: Read timed out"), False)

import tempfile
from pathlib import Path as _P
_out = _P(tempfile.mkdtemp(prefix="altar-season-"))
build.emit([], {"nica-nc":  {"status": "off-season", "off_season": True, "optional": False},
                "pisgah-rage": {"status": "cached", "optional": False},
                "bikereg":  {"status": "ok"}}, site=_out)
_report = json.loads((_out / "build-report.json").read_text())
check("off-season stays out of needs_attention",
      _report["needs_attention"], ["pisgah-rage"])
import shutil as _sh
_sh.rmtree(_out, ignore_errors=True)


print("\nmonth-only sources (regression: invented days that moved nightly)")
# Real shape from https://ashevilleonbikes.com/events, captured 2026-08-24: the
# hub page prints "Tour de Fat — OCT" and nothing more. The extractor used to
# be told to pick the day itself and picked a different one most nights.
AOB = {"id": "asheville-on-bikes", "name": "Asheville on Bikes", "trust": 80,
       "default_category": "group-ride", "org_url": "https://ashevilleonbikes.com/",
       "date_precision": "month"}
MONTH_ONLY = {"date_precision": "month", "all_day": True,
              "city": "Asheville", "state": "NC"}

# Two builds, two different guesses for the same October event.
monday  = normalize.prepare([{"title": "Tour de Fat", "start": "2026-10-03", **MONTH_ONLY}],
                            AOB, HOME, R)
tuesday = normalize.prepare([{"title": "Tour de Fat", "start": "2026-10-24", **MONTH_ONLY}],
                            AOB, HOME, R)
check("guessed day is discarded", monday[0]["start"], "2026-10-01")
check("flagged as month precision", monday[0]["date_precision"], "month")
check("no invented span", monday[0]["end"], "")
# This is the one that matters. A moving uid reaches a subscriber's calendar
# as a delete plus an add, every single morning.
check("uid holds still across a different guess",
      monday[0]["uid"], tuesday[0]["uid"])

# The flag is BLANKET, deliberately. Asking the extractor to mark its own
# month-only rows was tried on 2026-08-24 and failed inside a single build: it
# returned "Tour de Fat, 3 October" unflagged, next to a row whose description
# read "Specific date has not been announced."
unflagged = normalize.prepare(
    [{"title": "Pumpkin Pedaller", "start": "2026-10-17", "all_day": True,
      "city": "Asheville", "state": "NC"}], AOB, HOME, R)
check("a confident day from a month-only source is still snapped",
      unflagged[0]["start"], "2026-10-01")

# Which is exactly why a month-only source may not also read pages that carry
# real days. Pin it against the live config so the two cannot be recombined.
import yaml as _y
_srcs = _y.safe_load(open("sources.yml"))["sources"]
_both = [x["id"] for x in _srcs
         if x.get("date_precision") == "month" and x.get("extra_urls")]
check("no month-only source also reads dated event pages", _both, [])
check("the Asheville on Bikes event pages are their own source",
      any(x["id"] == "asheville-on-bikes-pages"
          and not x.get("date_precision") for x in _srcs), True)

october  = normalize.prepare([{"title": "Ride Your City", "start": "2026-10-09", **MONTH_ONLY}],
                             AOB, HOME, R)
november = normalize.prepare([{"title": "Ride Your City", "start": "2026-11-09", **MONTH_ONLY}],
                             AOB, HOME, R)
check("a genuinely different month is a different event",
      october[0]["uid"] != november[0]["uid"], True)

# Snapping must not retire an event that has not happened, and must not sort an
# undated entry above every real event in a month already under way.
this_month = datetime.now().strftime("%Y-%m")
today = datetime.now().date().isoformat()
current = normalize.prepare([{"title": "Summer Cycle", "start": f"{this_month}-28",
                              **MONTH_ONLY}], AOB, HOME, R)
check("current month survives the horizon floor", len(current), 1)
check("a month already under way anchors to today, not the 1st",
      current[0]["start"], today)
check("and its uid still keys on the month, so it does not move daily",
      current[0]["uid"],
      normalize.uid({"title": "Summer Cycle", "start": f"{this_month}-01",
                     "date_precision": "month"}))

# A source that knows the day absorbs the placeholder, and the KNOWN date is
# what publishes — even though the placeholder came from the more trusted org.
placeholder = normalize.prepare(
    [{"title": "Tour de Fat", "start": "2026-10-14", **MONTH_ONLY}], AOB, HOME, R)
confirmed = normalize.prepare(
    [{"title": "Tour de Fat", "start": "2026-10-03", "all_day": True,
      "city": "Asheville", "state": "NC"}],
    {"id": "bikereg", "name": "BikeReg", "trust": 60, "default_category": "race",
     "org_url": "https://www.bikereg.com/"}, HOME, R)
both = normalize.dedupe(placeholder + confirmed)
tdf = [e for e in both if e["title"] == "Tour de Fat"]
check("placeholder absorbed, not published beside the real one", len(tdf), 1)
check("the confirmed date wins", tdf[0]["start"], "2026-10-03")
check("and it is no longer flagged TBA", tdf[0].get("date_precision"), None)
check("placeholder's org still credited",
      "Asheville on Bikes" in tdf[0].get("also_listed_by", []), True)

# The event-pages sibling: no flag, so a real day survives intact.
pages = normalize.prepare(
    [{"title": "Summer Cycle 2026", "start": "2026-10-22", "all_day": True,
      "city": "Asheville", "state": "NC"}],
    {"id": "asheville-on-bikes-pages", "name": "Asheville on Bikes", "trust": 80,
     "default_category": "group-ride", "org_url": "https://ashevilleonbikes.com/"},
    HOME, R)
check("the event-pages source keeps its real day", pages[0]["start"], "2026-10-22")
check("and is not labelled TBA", pages[0].get("date_precision"), None)

# Sources without the flag are untouched, whatever the extractor claims.
plain = normalize.prepare([{"title": "Old Fort Fifty", "start": soon(30), "all_day": True,
                            "date_precision": "month",
                            "city": "Old Fort", "state": "NC"}],
                          {"id": "g5", "name": "G5", "trust": 80,
                           "default_category": "race", "org_url": "https://g5.org"},
                          HOME, R)
check("day-precision source keeps its day", plain[0]["start"], soon(30))
check("day-precision source carries no flag", plain[0].get("date_precision"), None)


print("\nreal-world titles from live sources (2026-08-17)")
# Captured from g5trailcollective.org/volunteer. G5 defaults to trail-work;
# the workshop must still land in clinic via category_basis, and "Trail Day"
# must not be read as a race.
for title, want in [
    ("Trek Tuesday - Kitsuma Trail Day", "trail-work"),
    ("Betty's & Jarret's Run Trail Day", "trail-work"),
    ("Lower Heartbreak Trail Day", "trail-work"),
    ("Women + Trail Skills Workshop", "clinic"),
    ("Fall Trail Weekend!  Nov 6-8", "trail-work"),
]:
    check(title[:44], normalize.classify({"title": title, "description": ""}, "trail-work")[0], want)

print("\n" + ("-" * 52))
if fails:
    print(f"{len(fails)} FAILURES")
    for f in fails:
        print("  " + f)
    raise SystemExit(1)
print("all checks passed")
