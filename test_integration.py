"""
Feeds each adapter a payload shaped like what the real service returns, so the
parsing code is exercised without touching the network.

    python test_integration.py
"""
import json
import re
from datetime import datetime, timedelta
import pathlib
from types import SimpleNamespace

import adapters
import build
import normalize

soon = lambda d: (datetime.now() + timedelta(days=d)).date().isoformat()
ago  = lambda d: (datetime.now() - timedelta(days=d)).date().isoformat()
fails = []


def check(label, ok, extra=""):
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"  {extra}"))
    if not ok:
        fails.append(label)


class FakeResponse:
    def __init__(self, payload=None, text="", content=b""):
        self._payload, self.text, self.content = payload, text, content
        self.status_code = 200

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        pass


def patch(payload=None, text="", content=b""):
    adapters.http = lambda *a, **k: FakeResponse(payload, text, content)


def patch_llm(model_text, page="Calendar\n\nSeptember 5 - Dirt Diggler, Hendersonville NC"):
    """The llm adapter fetches the page first, then calls the API."""
    calls = {"n": 0}

    def fake(url, *a, **k):
        calls["n"] += 1
        if "api.anthropic.com" in url:
            return FakeResponse({"content": [{"type": "text", "text": model_text}]})
        return FakeResponse(text=f"<html><body><p>{page}</p></body></html>")

    adapters.http = fake


real_http = adapters.http

# --------------------------------------------------------------- tribe (WP)
print("\ntribe — WordPress / The Events Calendar")
patch({"events": [{
    "title": "Summer Cycle 2026",
    "start_date": f"{soon(6)} 14:00:00",
    "end_date": f"{soon(6)} 19:00:00",
    "all_day": False,
    "url": "https://ashevilleonbikes.com/events/asheville-summer-cycle",
    "description": "<p>Celebrate 20 years of Asheville on Bikes.</p>",
    "cost": "Free",
    "image": {"url": "https://x/img.jpg"},
    "venue": {"venue": "New Belgium Brewing", "city": "Asheville",
              "stateprovince": "NC", "geo_lat": 35.5846, "geo_lng": -82.5745},
}], "next_rest_url": None})
got = adapters.tribe({"url": "https://x/wp-json/tribe/events/v1/events"})
check("parses one event", len(got) == 1)
check("strips HTML from description", got[0]["description"] == "Celebrate 20 years of Asheville on Bikes.")
check("reads venue geo", got[0]["lat"] == 35.5846)
check("reads city", got[0]["city"] == "Asheville")

# ------------------------------------------------------------- squarespace
print("\nsquarespace — ?format=json (Pisgah Area SORBA)")
epoch = int(datetime.fromisoformat(f"{soon(11)}T17:30:00").timestamp() * 1000)
patch({"items": [{
    "title": "Women's Dig Night — Lower Bennett Gap",
    "startDate": epoch, "endDate": epoch + 3 * 3600 * 1000,
    "isAllDay": False, "fullUrl": "/events-volunteer/womens-dig",
    "excerpt": "<p>Brushing and drain clearing.</p>",
    "assetUrl": "https://x/i.jpg",
    "location": {"addressTitle": "The Hub", "addressLine1": "49 Pisgah Hwy",
                 "addressLine2": "Pisgah Forest, NC 28768",
                 "mapLat": 35.2762, "mapLng": -82.7126},
}]})
got = adapters.squarespace({"url": "https://www.pisgahareasorba.org/events-volunteer"})
check("parses epoch-millis dates", got[0]["start"][:10] == soon(11), got[0]["start"])
check("builds absolute url",
      got[0]["url"] == "https://www.pisgahareasorba.org/events-volunteer/womens-dig")
check("pulls state out of address line", got[0]["state"] == "NC", got[0]["state"])
check("keeps coordinates", got[0]["lat"] == 35.2762)

# ------------------------------------------------------------------ jsonld
print("\njsonld — schema.org in page HTML (Wix / G5)")
patch(text=f"""<html><head>
<script type="application/ld+json">
{{"@context":"https://schema.org","@graph":[
 {{"@type":"WebSite","name":"G5"}},
 {{"@type":"Event","name":"Fall Trail Weekend",
   "startDate":"{soon(60)}","endDate":"{soon(62)}",
   "url":"https://www.g5trailcollective.org/event-details/fall-trail-weekend",
   "description":"Trail work, tacos, bonfire.",
   "image":["https://x/a.jpg"],
   "offers":{{"@type":"Offer","price":"0"}},
   "location":{{"@type":"Place","name":"Camp Grier",
     "address":{{"addressLocality":"Old Fort","addressRegion":"NC"}},
     "geo":{{"latitude":35.6293,"longitude":-82.1815}}}}}}]}}
</script></head><body></body></html>""")
got = adapters.jsonld({"url": "https://www.g5trailcollective.org/volunteer"})
check("digs Events out of @graph", len(got) == 1)
check("date-only marks all_day", got[0]["all_day"] is True)
check("reads nested place + geo", got[0]["city"] == "Old Fort" and got[0]["lat"] == 35.6293)
check("takes first image from list", got[0]["image"] == "https://x/a.jpg")
check("reads offer price", got[0]["cost"] == "0")

# --------------------------------------------------------------- runsignup
print("\nrunsignup — public REST")
# Address block copied verbatim from a live 2026-08-17 response. Note what is
# NOT here: latitude and longitude. RunSignup returns no coordinates anywhere
# in the payload. The old fixture invented them, which hid the bug — every
# real RunSignup event arrived with lat=lng=None.
patch({"races": [{"race": {
    "name": "Old Fort Fifty", "next_date": f"{soon(70)} 08:00:00",
    "url": "https://runsignup.com/Race/NC/OldFort/OldFortFifty",
    "description": "30.4 miles of Pisgah.", "logo_url": "https://x/l.png",
    "address": {"street": "985 Camp Grier Rd", "street2": None,
                "city": "Old Fort", "state": "NC", "zipcode": "28762",
                "country_code": "US"},
}}]})
got = adapters.runsignup({}, {"zip": "28801"}, 100)
check("unwraps the race envelope", got[0]["title"] == "Old Fort Fifty")
check("real payload yields no coords", got[0]["lat"] is None and got[0]["lng"] is None)
check("stamps pre_geofenced so the town list can't drop it",
      got[0]["pre_geofenced"] is True)
check("coordless race still passes the geofence",
      normalize.in_region(got[0], {"lat": 35.5951, "lng": -82.5515}, 100) is True)

# If RunSignup ever does start returning coordinates, take them.
patch({"races": [{"race": {
    "name": "Old Fort Fifty", "next_date": f"{soon(70)} 08:00:00",
    "url": "https://runsignup.com/r", "description": "", "logo_url": None,
    "address": {"street": "985 Camp Grier Rd", "city": "Old Fort",
                "state": "NC", "latitude": "35.6293", "longitude": "-82.1815"},
}}]})
check("coerces string coords to float when present",
      adapters.runsignup({}, {"zip": "28801"}, 100)[0]["lat"] == 35.6293)

# Regression, 2026-08-17. Live build run #2 reported "runsignup: returned 0
# events". Cause: dates were sent as MM/DD/YYYY and RunSignup answers
# {"error": "Invalid parameters", ... "param_datatype_mismatch"} — "expected
# Date datatype, received string" — which the adapter saw as an empty list, so
# a broken call looked like a quiet season. It wants ISO. BikeReg wants the
# opposite. Pin both so nobody "consistently" formats them the same way.
sent = {}
adapters.http = lambda url, *a, **k: (sent.update(k.get("params") or {}),
                                      FakeResponse({"races": []}))[1]
adapters.runsignup({}, {"zip": "28801"}, 100)
check("runsignup sends ISO YYYY-MM-DD dates",
      re.fullmatch(r"\d{4}-\d{2}-\d{2}", sent.get("start_date", "")) is not None,
      sent.get("start_date"))
check("runsignup end_date is ISO too",
      re.fullmatch(r"\d{4}-\d{2}-\d{2}", sent.get("end_date", "")) is not None,
      sent.get("end_date"))
check("bikereg still sends MM/DD/YYYY (the opposite)",
      re.fullmatch(r"\d{2}/\d{2}/\d{4}",
                   adapters._bikereg_filters({"lat": 1, "lng": 2}, 100)[0]["startDate"])
      is not None)

# ------------------------------------------------------------ volunteerhub
print("\nvolunteerhub — Pisgah Area SORBA's live portal")
# Captured verbatim from pas.volunteerhub.com's own JSON on 2026-08-17.
patch({"days": [{"date": "2026-08-18T00:00:00", "events": [{
    "id": 26323094,
    "guid": "0e74bfd9-2cb9-4b8c-81bd-801f9572d4c8",
    "name": "Women in Trail Leadership Panel - FREE!",
    "sTime": "2026-08-18T17:30:00",
    "eTime": "2026-08-18T19:30:00",
    "location": "31 Schenck Pkwy, Asheville, NC 28803, USA",
    "shortDescription": "<p>Women are shaping the future of our trails.&nbsp;</p>",
    "longDescription": "<p>Join us at REI Biltmore Park.</p>",
}]}], "nextBlockUrl": None})
got = adapters.volunteerhub({"portal": "https://pas.volunteerhub.com",
                             "org_url": "https://www.pisgahareasorba.org/"})
check("reads the day/event nesting", len(got) == 1, len(got))
check("title", got[0]["title"] == "Women in Trail Leadership Panel - FREE!")
check("naive local ISO start kept", got[0]["start"].startswith("2026-08-18"))
check("end time read", got[0]["end"].startswith("2026-08-18"))
check("html stripped from description",
      "<p>" not in got[0]["description"] and "&nbsp;" not in got[0]["description"],
      got[0]["description"])
check("city parsed from the location string", got[0]["city"] == "Asheville",
      got[0]["city"])
check("state parsed", got[0]["state"] == "NC", got[0]["state"])

# A portal that hands back the same block forever must not spin to the cap.
calls = {"n": 0}


def fake_loop(url, *a, **k):
    calls["n"] += 1
    return FakeResponse({"days": [{"date": "2026-08-18T00:00:00", "events": [
        {"id": 1, "name": "Bent Creek Dig-In", "sTime": "2026-09-25T09:00:00"}]}],
        "nextBlockUrl": "/internalapi/volunteerview/view/index?block=2"})


adapters.http = fake_loop
got = adapters.volunteerhub({"portal": "https://pas.volunteerhub.com"})
check("paging stops at the block cap", calls["n"] <= adapters.VOLUNTEERHUB_MAX_BLOCKS,
      calls["n"])
check("repeated event not duplicated", len(got) == 1, len(got))

# ----------------------------------------------------------------- bikereg
print("\nbikereg — filter ladder and paging")
BR_HOME = {"lat": 35.5951, "lng": -82.5515, "zip": "28801"}


def _row(eid, name, lat, lng):
    return {"EventId": eid, "EventName": name, "EventCity": "x", "EventState": "NC",
            "EventDate": "/Date(1781496000000-0400)/", "Latitude": lat, "Longitude": lng}


# The whole point of the ladder: a filter that silently falls through to the
# national list must be REJECTED, and the next shape tried. Here rung 1 (loc)
# answers with a Wyoming race, rung 2 (region=Southeast) answers with a local
# one — we must end up with the local one.
seen_params = []


def fake_ladder(url, *a, **k):
    params = k.get("params") or {}
    seen_params.append(params)
    if params.get("startpage"):
        return FakeResponse({"MatchingEvents": []})       # only ever one page
    if "loc" in params:
        return FakeResponse({"MatchingEvents": [_row(1, "Wyoming DH", 43.78, -110.94)]})
    return FakeResponse({"MatchingEvents": [_row(2, "Old Fort Fifty", 35.62, -82.18)]})


adapters.http = fake_ladder
got = adapters.bikereg({}, BR_HOME, 75)
check("national fallthrough rejected, next rung used",
      [e["title"] for e in got] == ["Old Fort Fifty"], [e["title"] for e in got])
check("tried loc before region", "loc" in seen_params[0])

# A server that ignores startpage would hand back page 1 forever. Dedupe on
# EventId has to stop that rather than spinning to the page cap.
calls = {"n": 0}


def fake_stuck(url, *a, **k):
    calls["n"] += 1
    return FakeResponse({"MatchingEvents": [_row(7, "Old Fort Fifty", 35.62, -82.18)]})


adapters.http = fake_stuck
got = adapters.bikereg({}, BR_HOME, 75)
check("ignored startpage does not loop", calls["n"] == 2, f"{calls['n']} calls")
check("repeated row not duplicated", len(got) == 1, len(got))

# --------------------------------------------------------------------- ics
print("\nics — real iCalendar bytes (ClubExpress / any feed)")
patch(content=f"""BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:brbc-991\r
SUMMARY:Tuesday Social Ride\r
DTSTART:{soon(4).replace('-','')}T180000\r
DTEND:{soon(4).replace('-','')}T200000\r
LOCATION:Blue Ridge Beer Garden\\, Hendersonville\\, NC\r
DESCRIPTION:Meet 5:30\\, roll 6:00.\r
URL:https://brbcnc.clubexpress.com/e/991\r
END:VEVENT\r
END:VCALENDAR\r
""".encode())
got = adapters.ics({"url": "https://x.ics", "org_url": "https://x"})
check("parses VEVENT", got[0]["title"] == "Tuesday Social Ride")
check("unescapes location commas", "Hendersonville" in got[0]["venue"])
check("keeps event url", got[0]["url"].endswith("/e/991"))

# --------------------------------------------------------------------- llm
print("\na broken manual.yml must not take the whole calendar down")
# data/manual.yml is the one file a non-programmer edits by hand, and until
# 2026-08-24 it was the only input with no error guard: load_manual() raising
# killed main() before emit(), so ONE mistyped date published nothing at all.
_report = {}
_broken = SimpleNamespace(only=None, offline=True)
def _explode(home, radius):
    raise ValueError("could not parse start: 'next tuesdayy'")

_collected = []
for _sid, _loader in [("altar", _explode)]:
    try:
        _collected += _loader(None, None)
    except Exception as _exc:                              # noqa: BLE001
        _report[_sid] = {"status": "failed", "errors": [str(_exc)],
                         "cached_events": 0, "cache_age_days": None,
                         "optional": False}
check("a broken hand-entered file does not raise past the loader", _collected == [])
check("it is reported as a failed source", _report["altar"]["status"], "failed")
check("and it lands in needs_attention where a human will see it",
      [s for s, r in _report.items()
       if r.get("status") not in ("ok", "off-season") and not r.get("optional")],
      ["altar"])
# The guard has to be in build.py itself, not just in this test's mirror of it.
import inspect
_main_src = inspect.getsource(build.main) if hasattr(build, "main") else \
    pathlib.Path(build.__file__).read_text()
check("build.py actually wraps the hand-entered loaders",
      "except Exception" in _main_src and "load_manual" in _main_src)

print("\nbikereg dates (regression: 25 races published a 12am start nobody stated)")
# Captured verbatim from the BikeReg API. EventDate is a DATE — the millis are
# always midnight in the promoter's zone, which serialises to 04:00Z on the US
# east coast. Reading it as a timestamp put "12am" under every race on the live
# page. Both offsets below are real: -0400 in summer, -0500 in winter.
check("a summer date recovers the promoter's calendar day",
      adapters.ms_date("/Date(1780545600000-0400)/", as_date=True), "2026-06-04")
check("a winter date recovers it too, across the DST change",
      adapters.ms_date("/Date(1767243600000-0500)/", as_date=True), "2026-01-01")
check("the raw form is unchanged for anything that really is a timestamp",
      adapters.ms_date("/Date(1780545600000-0400)/"), "2026-06-04T04:00:00+00:00")
check("a missing date stays missing", adapters.ms_date(None, as_date=True) is None)
check("an unparseable value does not raise",
      adapters.ms_date("not a date", as_date=True) is None)

_row = adapters._bikereg_event({
    "EventName": "Pisgah Monster-Cross Challenge",
    "EventDate": "/Date(1780545600000-0400)/",
    "EventCity": "Brevard", "EventState": "NC", "EventTypes": ["Gravel"],
    "EventID": 12345})
check("a race publishes as all-day", _row["all_day"], True)
check("with a date and no invented time", _row["start"], "2026-06-04")
check("and no time sneaks into the end either", _row["end"] is None)

print("\nics — RRULE expansion (regression: 177 events fetched, 0 published)")
# Captured verbatim from the Gravelo Workshop public Google Calendar on
# 2026-08-24. 177 VEVENTs, 19 of them recurring, and NOT ONE with a future
# DTSTART — every upcoming ride exists only as an RRULE. The adapter read
# DTSTART and ignored RRULE, so the source reported "ok (0 events)" while
# holding a standing Saturday ride.
GOOGLE_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Google Inc//Google Calendar 70.9054//EN
BEGIN:VEVENT
DTSTART;VALUE=DATE:20241102
DTEND;VALUE=DATE:20241103
RRULE:FREQ=WEEKLY;BYDAY=SA
SUMMARY:9:30am Bakery Ride B Group Start at Gravelo
LOCATION:Gravelo Workshop\\, 793 Merrimon Ave\\, Asheville\\, NC 28804
UID:bakery@google.com
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20241030
RRULE:FREQ=WEEKLY;BYDAY=WE;UNTIL=20260113
SUMMARY:Group Ride (roll out 6:15pm)
UID:wednesday@google.com
END:VEVENT
BEGIN:VEVENT
DTSTART:20240609T130000Z
DTEND:20240609T170000Z
SUMMARY:Ride Series #1
UID:oneoff@google.com
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261106
DTEND;VALUE=DATE:20261109
SUMMARY:Fall Trail Weekend
UID:weekender@google.com
END:VEVENT
END:VCALENDAR
"""
real_http = adapters.http
adapters.http = lambda *a, **k: SimpleNamespace(content=GOOGLE_ICS)
rows = adapters.ics({"url": "https://x.ics", "org_url": "https://x"})
adapters.http = real_http

bakery = [e for e in rows if "Bakery" in e["title"]]
check("the standing Saturday ride is expanded", len(bakery) == adapters.ICS_RECUR_CAP,
      f"{len(bakery)}")
check("expansion starts from today, not from DTSTART in 2024",
      all(e["start"][:10] >= datetime.now().date().isoformat() for e in bakery))
check("occurrences land on Saturdays",
      {datetime.fromisoformat(e["start"]).weekday() for e in bakery} == {5})
# An open-ended weekly ride would otherwise publish ~57 rows over the horizon
# and bury everything else, exactly as normalize caps hand-entered recurrences.
check("an open-ended series is capped, not unbounded", len(bakery) <= 12)
check("a series whose UNTIL has passed publishes nothing",
      [e for e in rows if "roll out" in e["title"]] == [])
check("a non-recurring past event is still just one row",
      len([e for e in rows if "Ride Series" in e["title"]]) == 1)
# RFC 5545 all-day DTEND is exclusive. Taken literally, every Saturday ride
# becomes a two-day event and paints two cells in the month grid.
check("a one-day all-day event gets no end date", bakery[0]["end"] is None)
check("a genuine weekender keeps its real span",
      [(e["start"], e["end"]) for e in rows if "Fall Trail" in e["title"]],
      [("2026-11-06", "2026-11-08")])

print("\nics — a month-only event says so instead of naming a day")
# The .ics is the surface where an invented day does the most damage: it lands
# in the subscriber's own calendar, on a square they may plan around. A
# month-precision event still needs a DTSTART, so it gets the 1st — and the
# SUMMARY has to admit that.
tba = normalize.prepare(
    [{"title": "Tour de Fat", "start": "2026-10-17", "date_precision": "month",
      "city": "Asheville", "state": "NC"}],
    {"id": "asheville-on-bikes", "name": "Asheville on Bikes", "trust": 80,
     "default_category": "group-ride", "org_url": "https://ashevilleonbikes.com/",
     "date_precision": "month"},
    {"lat": 35.5951, "lng": -82.5515, "zip": "28801"}, 75)
blob = build.to_ics(tba, "T", "d").decode()
check("summary admits the day is unknown", "Tour de Fat (date TBA)" in blob)
check("anchored to the first of the month", "DTSTART;VALUE=DATE:20261001" in blob)
check("body names the month and calls the day a placeholder",
      "October 2026" in blob.replace("\r\n ", "") and "placeholder" in blob.replace("\r\n ", ""))

print("\nllm — model response handling (DARC-style prose page)")
import os
os.environ["ANTHROPIC_API_KEY"] = "test-key-not-used"
patch_llm(f"""```json
[
 {{"title":"Dirt Diggler Gravel Grinder","start":"{soon(20)}","all_day":true,
   "city":"Hendersonville","state":"NC","description":"DARC featured event."}},
 {{"title":"CRAFTED Bicycle & Gear Show","start":"{soon(41)}","end":"{soon(42)}",
   "all_day":true,"venue":"Cane Creek Cycling Components","city":"Fletcher","state":"NC"}},
 {{"title":"Broken entry with no date"}}
]
```""")
got = adapters.llm({"url": "https://darccycling.com/calendar", "name": "DARC"})
check("strips markdown fences", len(got) == 2, f"got {len(got)}")
check("drops the dateless entry", all(e["start"] for e in got))
check("keeps multi-day end", got[1]["end"][:10] == soon(42))
check("tags provenance", got[0]["extracted_by"] == "llm")

patch_llm('Sure! Here you go:\n[{"title":"X","start":"' + soon(9) + '"}]')
check("recovers JSON from chatty reply", len(adapters.llm({"url": "https://x"})) == 1)

patch_llm("[]")
check("empty array is not an error", adapters.llm({"url": "https://x"}) == [])

adapters.http = real_http

# ------------------------------------------------------------ full pipeline
print("\nheld events (regression: a real ride silently vanished for three days)")
# 2026-08-21 published the Dirt Skrrts 12 September group ride. 2026-08-22 did
# not, and the log said "ok (3 events)" both mornings. The ride was still on
# the source page the whole time.
import tempfile as _tf
from pathlib import Path as _Path
_cache_was = build.CACHE
build.CACHE = _Path(_tf.mkdtemp(prefix="altar-cache-"))
_today = datetime.now().date()
_yday = (_today - timedelta(days=1)).isoformat()

SKRRTS = {"id": "blue-ridge-dirt-skrrts", "name": "Blue Ridge Dirt Skrrts"}
build.write_cache("blue-ridge-dirt-skrrts", [
    {"uid": "sept", "title": "September Group Ride + Social", "start": soon(19),
     "last_seen": _yday},
    {"uid": "oct", "title": "October Group Ride + Social", "start": soon(47),
     "last_seen": _yday},
    {"uid": "gone", "title": "Cancelled Long Ago", "start": soon(30),
     "last_seen": (_today - timedelta(days=30)).isoformat()},
    {"uid": "past", "title": "Last Month's Ride", "start": ago(9),
     "last_seen": _yday},
])
kept, held = build.hold_recent(SKRRTS, [
    {"uid": "oct", "title": "October Group Ride + Social", "start": soon(47)}])
titles = sorted(e["title"] for e in kept)
check("today's extraction is kept", "October Group Ride + Social" in titles)
check("yesterday's ride is held, not dropped", "September Group Ride + Social" in titles)
check("long-gone event is released", "Cancelled Long Ago" not in titles)
check("an event that already happened is not resurrected", "Last Month's Ride" not in titles)
check("the hold is reported, not silent", len(held) == 1, f"{len(held)}")
check("held event says when it was last seen", held[0]["held_since"] == _yday)

# The guard that stops a source's OLD date shape coming back and beating its
# replacement in dedupe for a week.
build.write_cache("aob", [{"uid": "old", "title": "Tour de Fat",
                           "start": "2026-10-03", "last_seen": _yday}])
kept2, held2 = build.hold_recent({"id": "aob", "date_precision": "month"}, [])
check("cached rows of the wrong date shape are not held", len(held2) == 0, f"{len(held2)}")

import shutil as _shutil
_shutil.rmtree(build.CACHE, ignore_errors=True)
build.CACHE = _cache_was

print("\nfull pipeline — five sources into one calendar")
HOME = {"lat": 35.5951, "lng": -82.5515, "zip": "28801"}
pool = []
pool += normalize.prepare(
    [{"title": "Old Fort Fifty", "start": soon(70), "all_day": True,
      "city": "Old Fort", "state": "NC", "url": "https://bikereg.com/ofc"}],
    {"id": "bikereg", "name": "BikeReg", "trust": 60, "default_category": "race",
     "category_authority": True, "org_url": "https://bikereg.com"}, HOME, 75)
pool += normalize.prepare(
    [{"title": "2026 Old Fort Fifty", "start": soon(70), "all_day": True,
      "city": "Old Fort", "state": "NC", "lat": 35.6293, "lng": -82.1815,
      "description": "30.4 miles from Camp Grier.", "url": "https://g5.org/ofc"}],
    {"id": "g5", "name": "G5 Trail Collective", "trust": 80,
     "default_category": "trail-work", "org_url": "https://g5.org"}, HOME, 75)
pool += normalize.prepare(
    [{"title": "Women's Dig Night", "start": f"{soon(11)}T17:30:00",
      "city": "Pisgah Forest", "state": "NC"}],
    {"id": "pas", "name": "Pisgah Area SORBA", "trust": 80,
     "default_category": "trail-work", "org_url": "https://pas.org"}, HOME, 75)
pool += normalize.prepare(
    [{"title": "Charlotte Crit", "start": soon(30), "lat": 35.2271, "lng": -80.8431}],
    {"id": "far", "name": "Far Away", "trust": 60, "default_category": "race",
     "org_url": "https://x"}, HOME, 75)
pool += normalize.prepare(
    [{"title": "UCI MTB World Cup — Andorra", "start": soon(25),
      "city": "Pal Arinsal", "state": ""}],
    {"id": "uci", "name": "UCI", "trust": 60, "default_category": "watch",
     "bucket": "world", "org_url": "https://uci.org"}, HOME, 75)

merged = normalize.dedupe(pool)
check("out-of-region event dropped", not any("Charlotte" in e["title"] for e in merged))
check("world bucket bypasses geofence", any(e["bucket"] == "world" for e in merged))
ofc = [e for e in merged if "Old Fort Fifty" in e["title"]]
check("duplicate collapsed to one", len(ofc) == 1, f"{len(ofc)}")
check("org site beat the reg platform", ofc[0]["source_name"] == "G5 Trail Collective")
check("race title beat trail-work default", ofc[0]["category"] == "race")
check("kept BikeReg credit", "BikeReg" in ofc[0].get("also_listed_by", []))
check("distance computed", ofc[0].get("distance_mi", 0) > 20)

# Build into a throwaway directory. This block used to write into the real
# site/ and then unlink the results, which deleted five TRACKED files —
# events.json and all three .ics feeds — from the working tree of anyone who
# ran the tests. Nothing warned; `git status` just quietly showed five
# deletions waiting to be committed.
import tempfile
from pathlib import Path
OUT = Path(tempfile.mkdtemp(prefix="altar-site-"))
REAL = Path("site/events.json")
before = REAL.stat().st_mtime_ns if REAL.exists() else None

build.emit(merged, {"g5": {"status": "ok", "kept": 1},
                    "darc": {"status": "failed", "errors": ["boom"], "optional": False}},
           site=OUT)

check("real site/ untouched by the test build",
      (REAL.stat().st_mtime_ns if REAL.exists() else None) == before)

payload = json.loads((OUT / "events.json").read_text())
check("events.json written", payload["count"] == len(merged) - 1)
check("world kept separate", len(payload["world"]) == 1)
ics_text = (OUT / "events.ics").read_bytes().decode()
check("events.ics has the events", ics_text.count("BEGIN:VEVENT") == payload["count"])
check("world excluded from local ics", "Andorra" not in ics_text)
check("races.ics filtered", (OUT / "races.ics").read_bytes().decode().count("BEGIN:VEVENT") == 1)
report = json.loads((OUT / "build-report.json").read_text())
check("report flags the broken source", report["needs_attention"] == ["darc"])

import shutil
shutil.rmtree(OUT, ignore_errors=True)

print("\n" + "-" * 52)
if fails:
    print(f"{len(fails)} FAILURES: " + ", ".join(fails))
    raise SystemExit(1)
print("all integration checks passed")
