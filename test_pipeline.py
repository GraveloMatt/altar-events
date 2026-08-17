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
