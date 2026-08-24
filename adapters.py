"""
Fetch strategies, one per kind of site.

Every adapter takes a source config dict and returns a list of raw event dicts
using the shared shape below. Nothing here dedupes, filters or geocodes —
that's normalize.py's job.

    {
      "title":       str            required
      "start":       ISO 8601 str   required
      "end":         ISO 8601 str   optional
      "all_day":     bool
      "url":         str            link to the event page
      "description": str
      "venue":       str
      "city":        str
      "state":       str
      "lat":         float | None
      "lng":         float | None
      "image":       str | None
      "cost":        str | None
    }

Design rule: never let one dead source kill the build. Adapters raise
SourceError, build.py catches it, logs it, and keeps the last good events for
that source from the previous run.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, time as _time, timedelta, timezone
from typing import Any, Iterable

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from dateutil.rrule import rrulestr

UA = "AltarCyclesEventBot/1.0 (+https://altar.bike/calendar; events@altar.bike)"
TIMEOUT = 25
EASTERN = "America/New_York"


class SourceError(Exception):
    """A source failed. Recoverable — build.py falls back to cached events."""


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def http(url: str, *, method: str = "GET", retries: int = 3, **kw) -> requests.Response:
    """One place for the user agent, timeouts and backoff."""
    headers = {"User-Agent": UA, "Accept": "*/*"}
    headers.update(kw.pop("headers", {}))
    last = None
    for attempt in range(retries):
        try:
            r = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code} from {url}")
            r.raise_for_status()
            return r
        except Exception as exc:               # noqa: BLE001 - retry anything transient
            last = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise SourceError(f"{url}: {last}")


def iso(value: Any, *, all_day: bool = False) -> str | None:
    """Parse whatever a site gives us into an ISO 8601 string."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):        # epoch millis (Wix) or seconds
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        dt = dateparser.parse(str(value))
    except (ValueError, OverflowError, TypeError):
        return None
    if dt is None:
        return None
    return dt.date().isoformat() if all_day else dt.isoformat()


def text_of(html: str) -> str:
    """Strip a page down to readable text so the LLM isn't paying for markup."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "svg", "noscript", "form"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n").splitlines()]
    return "\n".join(ln for ln in lines if ln)


_MS_DATE = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")


def ms_date(value: Any) -> str | None:
    """
    Parse ASP.NET's /Date(1780545600000-0400)/ into ISO 8601.

    BikeReg's REST API serialises every date this way. dateutil can't read it,
    so it has to be unwrapped before iso() sees it. The trailing offset is the
    promoter's local zone; the millis are already UTC epoch, so applying the
    offset again would double-count it. We keep the millis and ignore it.
    """
    if not isinstance(value, str):
        return iso(value)
    m = _MS_DATE.search(value)
    if not m:
        return iso(value)
    return iso(int(m.group(1)))


def _first(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return default


# --------------------------------------------------------------------------
# tier 1 — real feeds
# --------------------------------------------------------------------------

# A standing weekly ride would publish ~57 entries across the 400-day horizon
# and swamp everything else, so each series is capped. This is deliberately the
# same number normalize.RECUR_DEFAULT_COUNT uses for hand-entered and submitted
# recurrences — one rule for "how far ahead do we promise a standing ride", not
# two.
ICS_RECUR_CAP = 12
ICS_HORIZON_DAYS = 400
_ICS_SCAN_LIMIT = 5000          # backstop against a pathological rule


def _ics_occurrences(comp, start_val, all_day: bool) -> list:
    """Every future date one VEVENT actually means.

    An .ics feed is the best data this project gets, and until 2026-08-24 the
    adapter threw most of it away: it read DTSTART and ignored RRULE entirely.
    Google Calendar — and every other real calendar — writes a recurring event
    as ONE VEVENT whose DTSTART is the FIRST occurrence, sometimes years back,
    with the repetition in an RRULE. So a weekly ride looked like a single
    event that happened in 2024 and got dropped as past.

    gravelo-workshop is the case that exposed it: 177 VEVENTs fetched, 19 of
    them recurring, ZERO with a future DTSTART, and the source reported
    "ok (0 events)". Their standing Saturday ride — FREQ=WEEKLY;BYDAY=SA from
    2 Nov 2024 with no UNTIL — was invisible.
    """
    rule = comp.get("rrule")
    if rule is None:
        return [start_val]

    anchor = (start_val if isinstance(start_val, datetime)
              else datetime.combine(start_val, _time.min))
    anchor = anchor.replace(tzinfo=None)

    # Google writes UNTIL in UTC ("...Z") while DTSTART is naive or floating.
    # dateutil refuses to compare aware and naive datetimes, so the whole
    # expansion runs naive. These calendars are local to one town; the worst
    # this can cost is an hour at a DST boundary.
    text = re.sub(r"(UNTIL=\d{8}T\d{6})Z", r"\1", rule.to_ical().decode())
    try:
        series = rrulestr(text, dtstart=anchor)
    except (ValueError, TypeError):
        return [start_val]

    # Cancelled individual occurrences.
    skipped = set()
    exdate = comp.get("exdate")
    for block in (exdate if isinstance(exdate, list) else [exdate] if exdate else []):
        for entry in getattr(block, "dts", []):
            value = entry.dt
            skipped.add(value if isinstance(value, date) and not isinstance(value, datetime)
                        else value.replace(tzinfo=None).date())

    floor = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    ceiling = floor + timedelta(days=ICS_HORIZON_DAYS)
    out = []
    for seen, when in enumerate(series):
        if seen > _ICS_SCAN_LIMIT or when > ceiling:
            break
        if when < floor or when.date() in skipped:
            continue
        out.append(when.date() if all_day else when)
        if len(out) >= ICS_RECUR_CAP:
            break
    return out


def ics(source: dict) -> list[dict]:
    """A genuine .ics feed. The best case: exact data, and it never breaks."""
    from icalendar import Calendar

    raw = http(source["url"]).content
    out = []
    for comp in Calendar.from_ical(raw).walk("VEVENT"):
        start = comp.get("dtstart")
        if start is None:
            continue
        start_val = start.dt
        all_day = not isinstance(start_val, datetime)
        end = comp.get("dtend")
        geo = comp.get("geo")
        # Hold the event's LENGTH, not its end date: every occurrence of a
        # recurring event runs as long as the first one, but ends on its own
        # day. Copying the master's DTEND onto all of them would put every
        # Saturday ride's finish in November 2024.
        span = None
        if end is not None:
            try:
                span = end.dt - start_val
            except TypeError:
                span = None

        base = {
            "title": str(comp.get("summary", "")).strip(),
            "all_day": all_day,
            "url": str(comp.get("url", "") or source.get("org_url", "")),
            "description": _clean(str(comp.get("description", ""))),
            "venue": str(comp.get("location", "")),
            "lat": float(geo.latitude) if geo else None,
            "lng": float(geo.longitude) if geo else None,
            "uid_hint": str(comp.get("uid", "")),
        }
        # RFC 5545 makes an all-day DTEND EXCLUSIVE: a one-day event on the
        # 29th is written DTEND;VALUE=DATE:20241103-style, i.e. the 30th. Taken
        # literally that publishes every Saturday ride as a two-day event and
        # paints two cells in the month grid. Step back one day for all-day
        # events, and drop the end entirely if that leaves nothing.
        if span is not None and all_day:
            span = span - timedelta(days=1)
            if span.days < 1:
                span = None

        for when in _ics_occurrences(comp, start_val, all_day):
            out.append({**base,
                        "start": iso(when, all_day=all_day),
                        "end": iso(when + span, all_day=all_day) if span else None})
    return out


def tribe(source: dict) -> list[dict]:
    """WordPress + The Events Calendar. Paginated JSON REST, very common."""
    out, page, url = [], 1, source["url"]
    while page <= 10:
        r = http(url, params={"page": page, "per_page": 50, "status": "publish"})
        try:
            data = r.json()
        except ValueError as exc:
            raise SourceError(f"{url}: not JSON — plugin probably absent ({exc})")
        events = data.get("events", [])
        if not events:
            break
        for e in events:
            venue = e.get("venue") or {}
            out.append({
                "title": _clean(e.get("title", "")),
                "start": iso(_first(e, "utc_start_date", "start_date")),
                "end": iso(_first(e, "utc_end_date", "end_date")),
                "all_day": bool(e.get("all_day")),
                "url": e.get("url", ""),
                "description": _clean(e.get("description", "")),
                "venue": venue.get("venue", ""),
                "city": venue.get("city", ""),
                "state": venue.get("stateprovince", ""),
                "lat": _num(venue.get("geo_lat")),
                "lng": _num(venue.get("geo_lng")),
                "image": (e.get("image") or {}).get("url") if isinstance(e.get("image"), dict) else None,
                "cost": e.get("cost") or None,
            })
        if not data.get("next_rest_url"):
            break
        page += 1
    return out


def squarespace(source: dict) -> list[dict]:
    """
    Squarespace events collection. Appending ?format=json to any collection URL
    returns the underlying data. Times are epoch milliseconds, local to the site.
    """
    r = http(source["url"], params={"format": "json"})
    try:
        data = r.json()
    except ValueError as exc:
        raise SourceError(f"{source['url']}: ?format=json did not return JSON ({exc})")

    base = re.match(r"https?://[^/]+", source["url"]).group(0)
    out = []
    for item in data.get("items", []):
        start = item.get("startDate")
        if not start:
            continue
        loc = item.get("location") or {}
        addr = ", ".join(filter(None, [loc.get("addressLine1"), loc.get("addressLine2")]))
        out.append({
            "title": _clean(item.get("title", "")),
            "start": iso(start),
            "end": iso(item.get("endDate")),
            "all_day": bool(item.get("isAllDay")),
            "url": base + item.get("fullUrl", ""),
            "description": _clean(item.get("excerpt") or item.get("body") or ""),
            "venue": loc.get("addressTitle", ""),
            "city": _city_from(addr),
            "state": _state_from(addr),
            "lat": _num(loc.get("mapLat")),
            "lng": _num(loc.get("mapLng")),
            "image": item.get("assetUrl"),
        })
    return out


def clubexpress(source: dict) -> list[dict]:
    """
    ClubExpress. Exposes an iCal handler per club; if that 404s we fall back to
    the calendar module's JSON. Both keyed on club_id.
    """
    club = source["club_id"]
    host = source.get("host") or f"https://{source.get('subdomain', 'brbcnc')}.clubexpress.com"
    for path in (f"/handlers/ical.ashx?club_id={club}",
                 f"/content.aspx?page_id=4001&club_id={club}&format=ical"):
        try:
            return ics({**source, "url": host + path})
        except SourceError:
            continue
    raise SourceError(f"clubexpress {club}: no iCal handler responded — run probe.py")


def runsignup(source: dict, home: dict, radius: int) -> list[dict]:
    """
    RunSignup public REST. No key needed for public races.
    Mostly running events, so build.py applies require_keywords hard.

    VERIFIED 2026-08-17:
      * The endpoint answers anonymously — no key, no header.
      * `zipcode` + `radius` ARE honoured server-side. Proof: radius=5 around
        28801 returned 10 races, every one in Asheville or Woodfin, while
        radius=100 returned Stanley, Statesville, Troutman and Jonesborough TN.
        Unlike BikeReg, this filter does not silently fall through.
      * **The response carries NO coordinates.** There is no latitude,
        longitude, lat, lng or coord field anywhere in the payload — only
        address.street / city / state / zipcode / country_code. The previous
        code read race["latitude"] and address["latitude"], both of which are
        always None, so every RunSignup event arrived with lat=lng=None and
        then had to survive normalize.in_region()'s town-name fallback. Races
        in towns outside REGION_TOWNS — Statesville, Troutman, Morristown —
        were silently dropped even though the server had already confirmed
        they were inside our radius.
    Fix: because the radius filter is trustworthy, we stamp `pre_geofenced`
    and let normalize.in_region() accept it. The source's own radius_miles
    (100 in sources.yml) is what gets enforced, which is the intent.
    """
    # ISO, NOT MM/DD/YYYY. Confirmed live 2026-08-17: sending MM/DD/YYYY gets
    # {"error": "Invalid parameters", ... "param_datatype_mismatch"} — "expected
    # Date datatype, received string" — and the adapter then reports "returned 0
    # events" rather than an error, so it looks like a quiet season instead of a
    # broken call. That is exactly what run #2 of the live build showed.
    # BikeReg wants MM/DD/YYYY and RunSignup wants ISO; do not unify them.
    today = datetime.now().strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=400)).strftime("%Y-%m-%d")
    r = http("https://runsignup.com/rest/races", params={
        "format": "json",
        "zipcode": home["zip"],
        "radius": radius,
        "start_date": today,
        "end_date": end,
        "events": "T",
        "only_partner_races": "F",
        "results_per_page": 250,
    })
    out = []
    for wrapper in r.json().get("races", []):
        race = wrapper.get("race", wrapper)
        addr = race.get("address") or {}
        out.append({
            "title": _clean(race.get("name", "")),
            "start": iso(race.get("next_date") or race.get("next_start_time")),
            "end": iso(race.get("next_end_date")),
            "all_day": False,
            "url": race.get("url", ""),
            "description": _clean(race.get("description", ""))[:800],
            "venue": _clean(addr.get("street", "")),
            "city": addr.get("city", ""),
            "state": addr.get("state", ""),
            # Kept in case RunSignup ever starts returning coordinates; as of
            # 2026-08-17 both are always None. See docstring.
            "lat": _num(race.get("latitude") or addr.get("latitude")),
            "lng": _num(race.get("longitude") or addr.get("longitude")),
            "image": race.get("logo_url"),
            # The server already applied zipcode+radius. Without this flag the
            # town-name fallback throws away everything outside REGION_TOWNS.
            "pre_geofenced": True,
        })
    return out


BIKEREG_SEARCH = "https://www.bikereg.com/api/search"

# VERIFIED 2026-08-17 against BikeReg's own API docs at
# https://www.bikereg.com/api/EventSearchDoc.aspx — real documentation, worth
# re-reading if this ever breaks. Documented params: name, region, states,
# loc, distance, eventtype, permit, startpage, year, startDate, endDate,
# eventID. Two rules from the docs that shape everything below:
#
#   "Results are limited to the first 100 matches and ordered chronologically
#    ascending."
#   "If all date parameters are absent (year, startdate, enddate), results
#    will include all future events."
#
# The trap that cost the previous session: an UNRECOGNISED param is silently
# dropped rather than erroring, so a bad filter returns the national list and
# looks like success. Confirmed ignored: `state`, `states=NC` in some shapes,
# `region=North Carolina`, `region=Mid-Atlantic` (hyphen). Confirmed working:
# `region=Southeast` (the region that actually contains NC), `region=Mid
# Atlantic` and `region=New England` (SPACES, not hyphens — the hyphenated
# spelling in site URLs like /events/Cyclocross/Mid-Atlantic is a web route
# slug, not the API value).
#
# We lead with `loc`+`distance` rather than any region name because it is an
# exact radius instead of a coarse bucket: `region=Southeast` mixes in
# Maryland and Virginia events 400 miles away while `loc` returns the TN/SC/GA
# events that are genuinely close to Asheville, and every row comes back with
# a `Distance` in miles we can re-check. The region and states rungs stay as
# fallbacks in case BikeReg ever retires `loc`.
BIKEREG_SEARCH = "https://www.bikereg.com/api/search"
BIKEREG_DOCS = "https://www.bikereg.com/api/EventSearchDoc.aspx"


def _bikereg_filters(home: dict, radius: int) -> tuple[dict, ...]:
    """Filter shapes to try, best first. Dates are MM/DD/YYYY (confirmed)."""
    start = datetime.now().strftime("%m/%d/%Y")
    end = (datetime.now() + timedelta(days=400)).strftime("%m/%d/%Y")
    dates = {"startDate": start, "endDate": end}
    return (
        # CONFIRMED WORKING: loc is "<lat>|<lng>", distance is statute miles.
        {"loc": f"{home['lat']}|{home['lng']}", "distance": radius, **dates},
        {"region": "Southeast", **dates},        # confirmed; NC lives here
        {"states": "NC,SC,TN,GA,VA", **dates},
        dates,                                   # dated but unfiltered
        {},                                      # last resort, national cap
    )


def bikereg(source: dict, home: dict, radius: int) -> list[dict]:
    """
    BikeReg powers registration for essentially every sanctioned race in the
    Southeast, which makes it the highest-yield source in sources.yml.

    Uses the documented public REST search (no key). Results cap at 100 rows
    ordered by date ascending, so the filter matters: unfiltered, the first
    100 national events can easily contain nothing within 100 miles of
    Asheville. We page with `startpage` until a page comes back empty.

    Self-verifying by design: we keep the first filter shape that returns
    events actually near home. If BikeReg renames a param, the ladder finds
    the next one that works instead of quietly returning races in Wyoming.

    There is also a GraphQL API, which the docs recommend as faster. It is NOT
    at bikereg.com/graphql — BikeReg is an Outside property and the gateway is
    https://outsideapi.com/fed-gw/graphql, taking appType: BIKEREG. Untested
    here; the REST path is verified and sufficient, so it stays unused. If you
    ever need the extra speed, that's the endpoint to probe.
    """
    best: list[dict] = []
    for params in _bikereg_filters(home, radius):
        events = _bikereg_pages(params)
        if not events:
            continue
        if any(_within(e, home, radius) for e in events):
            return events           # this filter reached our region — keep it all
        best = best or events       # remember the first usable response
    if best:
        return best

    # Last resort: read the public listing page with Claude. Slower, lossier,
    # and costs a fraction of a cent — but it degrades instead of breaking.
    return llm({**source,
                "url": "https://www.bikereg.com/events?orc=1&region=NORTH+CAROLINA",
                "hint": "This is a race registration listing. Extract every "
                        "upcoming race with its date, name, city and state."})


BIKEREG_MAX_PAGES = 10          # 100 rows/page; a hard stop, not an expectation


def _bikereg_pages(params: dict) -> list[dict]:
    """
    Run one filter shape, following `startpage` until a page comes back empty.

    The docs contradict themselves on page size — "limited to the first 100
    matches" in one line, "a startpage of 2 returns the 200-300th results" in
    another — so we do not compute an offset. We just walk pages until one is
    empty, which is correct under either reading. Rows are deduped by EventId
    because a server that ignores `startpage` would otherwise hand us the same
    page forever.
    """
    out, seen = [], set()
    for page in range(1, BIKEREG_MAX_PAGES + 1):
        query = dict(params)
        if page > 1:
            query["startpage"] = page
        try:
            r = http(BIKEREG_SEARCH, params=query)
            rows = r.json().get("MatchingEvents") or []
        except (SourceError, ValueError, AttributeError, KeyError):
            break
        fresh = [e for e in rows if e.get("EventId") not in seen]
        if not fresh:
            break                   # empty page, or the server ignored startpage
        seen.update(e.get("EventId") for e in fresh)
        out.extend(_bikereg_event(e) for e in fresh)
    return [e for e in out if e["title"] and e["start"]]


def _bikereg_event(e: dict) -> dict:
    """One MatchingEvents row -> the shared event shape."""
    types = [t for t in (e.get("EventTypes") or []) if t]
    return {
        "title": _clean(e.get("EventName", "")),
        "start": ms_date(e.get("EventDate")),
        "end": ms_date(e.get("EventEndDate")),
        "all_day": False,
        # EventUrl is promoter-supplied and occasionally malformed (we saw
        # "http://www.BikeReg.comhttps://..." in live data). EventPermalink is
        # generated from the event id, so it is always well formed.
        "url": _bikereg_url(e),
        "description": _clean(e.get("EventNotes") or "")[:800],
        "venue": _clean(e.get("EventAddress", "")),
        "city": e.get("EventCity", ""),
        "state": e.get("EventState", ""),
        "lat": _num(e.get("Latitude")),
        "lng": _num(e.get("Longitude")),
        "image": e.get("EventLogo") or e.get("CoverPhoto"),
        # EventTypes is BikeReg's own discipline tagging ("Gravel",
        # "Mountain Bike", "Cyclocross"). This source is category_authority,
        # so it's better than guessing from the title.
        "discipline_hint": types[0] if types else None,
        "tags": types,
    }


def _bikereg_url(e: dict) -> str:
    url = (e.get("EventUrl") or "").strip()
    if url.count("http") == 1 and url.startswith("http"):
        return url
    return (e.get("EventPermalink") or "").strip()


def _within(event: dict, home: dict, radius: int) -> bool:
    from normalize import haversine
    if event.get("lat") is None or event.get("lng") is None:
        return False
    return haversine(home["lat"], home["lng"], event["lat"], event["lng"]) <= radius


def volunteerhub(source: dict) -> list[dict]:
    """
    VolunteerHub volunteer portals — where trail orgs actually keep dig days.

    VERIFIED 2026-08-17 against pas.volunteerhub.com. Pisgah Area SORBA's
    Squarespace events page is abandoned (last entry 21 Feb 2026) and this is
    their live system of record. It needs NO sign-in: the portal renders a
    public list, and the JSON behind it answered anonymously with eight real
    upcoming events including "Dirt Skrrts Bent Creek Work Day" and a
    "Women's Bikepacking Overnight".

    CAVEAT, read before relying on this: the endpoint is
    `/internalapi/volunteerview/view/index`, and the vendor named it
    *internalapi* for a reason — it is the portal's own SPA backend, not a
    documented public API, so VolunteerHub may change or remove it without
    notice. That is why this adapter is used on an `optional` source and falls
    back rather than raising. It reads only what the public page already shows
    and sends no credentials. If a source ever needs auth to see the list,
    stop — that is the BRBC situation and we do not go there.

    Shape: {days: [{date, events: [{name, sTime, eTime, location,
    shortDescription, longDescription, id, ...}]}], nextBlockUrl}
    Times are naive local ISO ("2026-08-18T17:30:00").
    """
    base = source["portal"].rstrip("/")
    out, url, seen = [], f"{base}/internalapi/volunteerview/view/index", set()

    for _ in range(VOLUNTEERHUB_MAX_BLOCKS):
        try:
            r = http(url, headers={"Accept": "application/json"})
            payload = r.json()
        except (SourceError, ValueError, AttributeError) as exc:
            if out:
                break               # keep what we already have
            raise SourceError(f"volunteerhub {base}: {exc}")

        for day in payload.get("days") or []:
            for e in day.get("events") or []:
                eid = e.get("id") or e.get("guid")
                if eid in seen:
                    continue
                seen.add(eid)
                desc = _clean(text_of(e.get("longDescription")
                                      or e.get("shortDescription") or ""))
                out.append({
                    "title": _clean(e.get("name", "")),
                    "start": iso(e.get("sTime")),
                    "end": iso(e.get("eTime")),
                    "all_day": False,
                    "url": source.get("org_url", base),
                    "description": desc[:800],
                    "venue": _clean(e.get("location") or ""),
                    "city": _city_from(e.get("location") or ""),
                    "state": _state_from(e.get("location") or ""),
                })

        nxt = payload.get("nextBlockUrl")
        if not nxt:
            break
        url = nxt if nxt.startswith("http") else f"{base}{nxt}"

    return [e for e in out if e["title"] and e["start"]]


VOLUNTEERHUB_MAX_BLOCKS = 6     # hard stop; the portal pages by date block


def ridewithgps(source: dict) -> list[dict]:
    """
    Ride with GPS club events — where most clubs actually keep group rides.

    Their v1 API requires an API client key on EVERY request, and the public
    club page renders its event list client-side, so there is nothing to
    scrape without credentials. Get a key at ridewithgps.com/api/v1/doc
    (free, self-serve) and set RWGPS_API_KEY + RWGPS_AUTH_TOKEN. Until then
    this source is inert rather than a build failure.
    """
    key = os.environ.get("RWGPS_API_KEY")
    if not key:
        raise SourceError(
            "ridewithgps needs RWGPS_API_KEY — request a client at "
            "ridewithgps.com/api/v1/doc, then add it as a repo secret")
    club = source["club_id"]
    r = http(f"https://ridewithgps.com/api/v1/clubs/{club}/events.json",
             params={"page_size": 100},
             headers={"Authorization": f"Bearer {os.environ.get('RWGPS_AUTH_TOKEN', key)}",
                      "x-rwgps-api-key": key})
    try:
        payload = r.json()
    except ValueError as exc:
        raise SourceError(f"rwgps club {club}: {exc}")
    events = (payload.get("club_events") or payload.get("events")
              or payload.get("event_series") or [])
    return [{
        "title": _clean(e.get("name", "")),
        "start": iso(e.get("starts_at") or e.get("start_date")),
        "end": iso(e.get("ends_at")),
        "all_day": bool(e.get("all_day")),
        "url": f"https://ridewithgps.com/events/{e.get('id')}" if e.get("id") else source.get("org_url", ""),
        "description": _clean(e.get("desc") or e.get("description") or ""),
        "venue": _clean(e.get("location") or ""),
        "lat": _num(e.get("lat")),
        "lng": _num(e.get("lng")),
    } for e in events if e.get("name")]


def wix(source: dict) -> list[dict]:
    """
    Wix Events. The public widget API is unstable across sites, so we read the
    JSON-LD that Wix renders into event pages instead — same data, stable shape.
    """
    try:
        found = jsonld(source)
        if found:
            return found
    except SourceError:
        pass
    raise SourceError("wix: no JSON-LD events found — falling through to llm")


def jsonld(source: dict) -> list[dict]:
    """
    schema.org Event blocks. Present on Wix, Eventbrite, most modern CMS
    themes, and anything that cares about Google rich results.
    """
    html = http(source["url"]).text
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            blob = json.loads(tag.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk_jsonld(blob):
            types = node.get("@type", "")
            types = types if isinstance(types, list) else [types]
            if not any("Event" in str(t) for t in types):
                continue
            loc = node.get("location") or {}
            loc = loc[0] if isinstance(loc, list) and loc else loc
            addr = loc.get("address") if isinstance(loc, dict) else {}
            addr = addr if isinstance(addr, dict) else {}
            geo = loc.get("geo") if isinstance(loc, dict) else {}
            geo = geo if isinstance(geo, dict) else {}
            offers = node.get("offers") or {}
            offers = offers[0] if isinstance(offers, list) and offers else offers
            out.append({
                "title": _clean(str(node.get("name", ""))),
                "start": iso(node.get("startDate")),
                "end": iso(node.get("endDate")),
                "all_day": len(str(node.get("startDate", ""))) == 10,
                "url": node.get("url") or source["url"],
                "description": _clean(str(node.get("description", ""))),
                "venue": loc.get("name", "") if isinstance(loc, dict) else "",
                "city": addr.get("addressLocality", ""),
                "state": addr.get("addressRegion", ""),
                "lat": _num(geo.get("latitude")),
                "lng": _num(geo.get("longitude")),
                "image": _image_of(node),
                "cost": str(offers.get("price")) if isinstance(offers, dict) and offers.get("price") else None,
            })
    if not out:
        raise SourceError(f"{source['url']}: no schema.org Event blocks")
    return out


def rss(source: dict) -> list[dict]:
    """Last resort structured option. Dates are publish dates, so low quality."""
    soup = BeautifulSoup(http(source["url"]).content, "xml")
    out = []
    for item in soup.find_all(["item", "entry"]):
        title = item.find("title")
        link = item.find("link")
        date = item.find(["pubDate", "published", "updated", "start"])
        if not (title and date):
            continue
        out.append({
            "title": _clean(title.get_text()),
            "start": iso(date.get_text()),
            "all_day": True,
            "url": link.get("href") if link and link.get("href") else (link.get_text() if link else ""),
            "description": _clean((item.find(["description", "summary"]) or title).get_text())[:600],
        })
    return out


# --------------------------------------------------------------------------
# tier 2 — the universal fallback
# --------------------------------------------------------------------------

LLM_SYSTEM = """You extract cycling events from web pages into JSON.

Return ONLY a JSON array. No prose, no markdown fences. Each element:
{"title","start","end","all_day","venue","city","state","description","cost","url"}

Rules:
- "start"/"end" are ISO 8601. Use YYYY-MM-DD for all-day events and set
  "all_day": true. Include a time only when the page states one.
- The page may omit the year. Infer it from context so the date lands in the
  future relative to TODAY, which is given below.
- A date range ("Sept 26-27") is ONE event with a start and an end.
- A recurring series ("every third Tuesday") is one entry; put the recurrence
  wording in "description".
- Skip anything already finished, and skip navigation, sponsors and past
  results. If the page has no events at all, return [].
- Never invent a date. Omit the event instead."""


def llm(source: dict) -> list[dict]:
    """
    Claude reads the page. Slower and marginally costly, but it survives
    redesigns, handles hand-typed prose calendars (DARC), and needs no
    per-site selectors. Roughly $0.001 per page on Haiku.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SourceError("llm adapter needs ANTHROPIC_API_KEY")

    page = text_of(http(source["url"]).text)[:60_000]
    if not page.strip():
        raise SourceError(f"{source['url']}: empty page")

    prompt = (
        f"TODAY: {datetime.now().date().isoformat()}\n"
        f"SOURCE: {source.get('name', source['url'])}\n"
        f"PAGE URL: {source['url']}\n"
    )
    if source.get("hint"):
        prompt += f"NOTE: {source['hint']}\n"
    prompt += f"\n---\n{page}\n---\n\nExtract the events as a JSON array."

    r = http("https://api.anthropic.com/v1/messages", method="POST",
             headers={"x-api-key": key,
                      "anthropic-version": "2023-06-01",
                      "content-type": "application/json"},
             json={"model": os.environ.get("EXTRACTION_MODEL", "claude-haiku-4-5-20251001"),
                   "max_tokens": 8000,
                   "system": LLM_SYSTEM,
                   "messages": [{"role": "user", "content": prompt}]})

    text = "".join(b.get("text", "") for b in r.json().get("content", [])
                   if b.get("type") == "text").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            raise SourceError(f"{source['url']}: model returned no JSON array")
        parsed = json.loads(match.group(0))

    out = []
    for e in parsed if isinstance(parsed, list) else []:
        if not isinstance(e, dict) or not e.get("title") or not e.get("start"):
            continue
        out.append({
            "title": _clean(str(e["title"])),
            "start": iso(e["start"], all_day=bool(e.get("all_day"))),
            "end": iso(e.get("end"), all_day=bool(e.get("all_day"))),
            "all_day": bool(e.get("all_day")),
            "url": e.get("url") or source["url"],
            "description": _clean(str(e.get("description", "")))[:800],
            "venue": _clean(str(e.get("venue", ""))),
            "city": _clean(str(e.get("city", ""))),
            "state": _clean(str(e.get("state", ""))),
            "cost": e.get("cost") or None,
            # Some pages publish a month and no day. The model is asked to SAY
            # so rather than invent one; normalize decides what to do with it,
            # and only for sources configured to allow it.
            "date_precision": ("month" if str(e.get("date_precision", "")).lower() == "month"
                               else None),
            "extracted_by": "llm",
        })
    return out


# --------------------------------------------------------------------------
# small utilities
# --------------------------------------------------------------------------

def _clean(s: str) -> str:
    s = BeautifulSoup(s or "", "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", s).strip()


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _walk_jsonld(node) -> Iterable[dict]:
    """JSON-LD nests Events inside @graph, itemListElement, arrays, subEvent."""
    if isinstance(node, list):
        for item in node:
            yield from _walk_jsonld(item)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item", "subEvent", "event"):
            if key in node:
                yield from _walk_jsonld(node[key])


def _image_of(node: dict) -> str | None:
    img = node.get("image")
    if isinstance(img, list) and img:
        img = img[0]
    if isinstance(img, dict):
        img = img.get("url")
    return img if isinstance(img, str) else None


_STATE_RE = re.compile(r",\s*([A-Z]{2})\b")


def _state_from(addr: str) -> str:
    m = _STATE_RE.search(addr or "")
    return m.group(1) if m else ""


_COUNTRY_TAIL = {"usa", "us", "u.s.", "u.s.a.", "united states",
                 "united states of america"}


def _city_from(addr: str) -> str:
    """
    Second-to-last comma segment, after dropping a trailing country.

    VolunteerHub writes Google-style addresses that end in the country —
    "31 Schenck Pkwy, Asheville, NC 28803, USA" — and without the strip the
    naive parts[-2] returns "NC 28803" as the city, which then fails the
    REGION_TOWNS geofence for any event with no coordinates.
    """
    parts = [p.strip() for p in (addr or "").split(",") if p.strip()]
    if parts and parts[-1].lower() in _COUNTRY_TAIL:
        parts = parts[:-1]
    return parts[-2] if len(parts) >= 2 else ""


REGISTRY = {
    "ics": ics,
    "tribe": tribe,
    "squarespace": squarespace,
    "clubexpress": clubexpress,
    "runsignup": runsignup,
    "bikereg": bikereg,
    "ridewithgps": ridewithgps,
    "volunteerhub": volunteerhub,
    "wix": wix,
    "jsonld": jsonld,
    "rss": rss,
    "llm": llm,
}

NEEDS_GEO = {"runsignup", "bikereg"}    # adapters that take home/radius args
