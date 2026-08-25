#!/usr/bin/env python3
"""
Build the Altar Cycles events calendar.

    python build.py                 # everything
    python build.py --only darc     # one source, for debugging
    python build.py --offline       # rebuild outputs from cache, no network

Writes into site/:
    events.json        the calendar the website reads
    events.ics         subscribe in Google / Apple / Outlook
    races.ics          just the races
    trail-work.ics     just the dig days
    build-report.json  what worked, what didn't, how stale each source is
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

import adapters
import normalize

ROOT = Path(__file__).parent
# Where emit() writes. Overridable because test_integration.py used to build
# into the REAL site/ and then unlink events.json and all three .ics feeds on
# its way out, leaving those tracked files deleted in the working tree. Commit
# that by accident and every subscribe link on the live calendar 404s until the
# next scheduled build. Tests now pass their own directory to emit().
SITE = Path(os.environ.get("ALTAR_SITE_DIR") or (ROOT / "site"))
CACHE = ROOT / "data" / "cache"
STALE_AFTER_DAYS = 14           # warn when a source hasn't succeeded in this long


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------

def run_adapter(spec: dict, home: dict, radius: int) -> list[dict]:
    fn = adapters.REGISTRY.get(spec["adapter"])
    if fn is None:
        raise adapters.SourceError(f"unknown adapter '{spec['adapter']}'")
    if spec["adapter"] in adapters.NEEDS_GEO:
        return fn(spec, home, spec.get("radius_miles", radius))

    out = fn(spec)
    # Some orgs split their calendar across a hub page plus one page per
    # event (Asheville on Bikes does exactly this). extra_urls reads those
    # too; a dead sub-page is skipped, not fatal.
    for extra in spec.get("extra_urls", []):
        try:
            out += fn({**spec, "url": extra})
        except adapters.SourceError as exc:
            print(f"        (skipped {extra}: {str(exc)[:56]})")
    return out


# An adapter can fail two very different ways: the source answered and had
# nothing, or the source did not answer. Only the first is ever "expected".
_EMPTY_SIGNS = ("returned 0 events", "no schema.org event blocks",
                "no events found", "empty page")


def looks_empty(error: str) -> bool:
    return any(sign in error.lower() for sign in _EMPTY_SIGNS)


def in_season(source: dict, today: date | None = None) -> bool:
    """Is this source expected to have anything at all right now?

    NICA NC is a spring league: it races late January to June and opens
    registration on 1 November. Between July and October its calendar page is
    legitimately, correctly empty — and it was reported as FAIL every single
    morning regardless, alongside pisgah-rage, which mirrors it. A red flag
    that is always red is not a flag, it is wallpaper. This project has already
    shipped three silent source failures that read as "quiet season"; the way
    that keeps happening is by training everyone to scroll past the warning.

    A source declares `season: [11, 6]` — November through June, wrapping the
    year. Outside that window an EMPTY result is reported as `off-season` and
    kept out of needs_attention. Nothing else is excused: an off-season source
    that 500s, times out, or stops resolving still flags, because that is a
    real breakage whatever the month.
    """
    season = source.get("season")
    if not season or len(season) != 2:
        return True
    start, end = int(season[0]), int(season[1])
    month = (today or date.today()).month
    return start <= month <= end if start <= end else (month >= start or month <= end)


def fetch_source(source: dict, home: dict, radius: int, report: dict) -> list[dict]:
    """
    Try the declared adapter, then each fallback in turn. If everything fails,
    serve the last good result from cache so one broken site never blanks the
    calendar. Sources marked optional fail silently.
    """
    sid = source["id"]
    attempts: list[dict] = [source] + [
        {**source, **fb} for fb in source.get("fallback", [])
    ]
    errors = []

    for attempt in attempts:
        try:
            raw = run_adapter(attempt, home, radius)
            if not raw:
                errors.append(f"{attempt['adapter']}: returned 0 events")
                continue
            events = normalize.prepare(raw, source, home, radius)
            events, held = hold_recent(source, events)
            write_cache(sid, events)
            report[sid] = {
                "status": "ok",
                "adapter": attempt["adapter"],
                "fetched": len(raw),
                "kept": len(events),
                "held": len(held),
                "at": datetime.now(timezone.utc).isoformat(),
            }
            print(f"  ok    {sid:26} {attempt['adapter']:12} "
                  f"{len(raw):3} fetched -> {len(events):3} kept")
            if held:
                # Say it out loud. A held event is one this run did NOT find,
                # and a source that holds the same event every day is a source
                # whose extraction has genuinely stopped working.
                print(f"        -> holding {len(held)} not returned this run: "
                      + ", ".join(f"{e['title'][:32]} ({e['held_since']})" for e in held[:4]))
            return events
        except Exception as exc:                      # noqa: BLE001
            errors.append(f"{attempt['adapter']}: {exc}")

    cached, age = read_cache(sid)
    off_season = bool(errors) and all(map(looks_empty, errors)) and not in_season(source)
    if off_season:
        status, level = "off-season", "quiet"
    else:
        status = "cached" if cached else "failed"
        level = "warn" if source.get("optional") else "FAIL"
    report[sid] = {
        "status": status,
        "errors": errors,
        "cached_events": len(cached),
        "cache_age_days": age,
        "optional": bool(source.get("optional")),
        "off_season": off_season,
    }
    print(f"  {level:5} {sid:26} {errors[-1][:70] if errors else 'no adapters'}")
    if cached:
        print(f"        -> serving {len(cached)} cached events ({age}d old)")
    return cached


# How long an event that has already been published survives a source that
# stops mentioning it.
#
# The llm adapter is not deterministic, and until 2026-08-24 nothing noticed.
# Blue Ridge Dirt Skrrts' September group ride (Sat 12 Sep, Fonta Flora State
# Trail) published normally in the 21 Aug build. On the 22nd the extractor
# simply did not return it. It has been missing every morning since, and the
# build log said "blue-ridge-dirt-skrrts — ok (3 events)" each time. The ride
# is still on the source page. Nothing was broken and nothing warned — the
# calendar just quietly lost the nearest group ride it had.
#
# One quiet run is not evidence an event was cancelled. A future event that was
# seen recently is held for this many days before it is allowed to fall off,
# and the hold is PRINTED and counted in the report, so it is never silent in
# the other direction either.
EVENT_GRACE_DAYS = 7


def hold_recent(source: dict, fresh: list[dict]) -> tuple[list[dict], list[dict]]:
    """Union this run's extraction with events still inside their grace window."""
    sid = source["id"]
    today = datetime.now().date()
    for e in fresh:
        e["last_seen"] = today.isoformat()

    path = CACHE / f"{sid}.json"
    if not path.exists():
        return fresh, []
    try:
        previous = json.loads(path.read_text()).get("events", [])
    except (ValueError, OSError):
        return fresh, []

    want = source.get("date_precision") or None
    seen_now = {e.get("uid") for e in fresh}
    held = []
    for e in previous:
        if e.get("uid") in seen_now:
            continue
        if (e.get("start") or "")[:10] < today.isoformat():
            continue                        # already happened; let it go
        if (e.get("date_precision") or None) != want:
            # The source's date handling changed under us — this is how the
            # old invented-day Asheville on Bikes rows would otherwise have
            # come back for a week and beaten their own replacements in
            # dedupe. A cached event of the wrong shape is stale, not held.
            continue
        try:
            last = date.fromisoformat(e.get("last_seen") or "")
        except ValueError:
            continue                        # written before events were stamped
        if (today - last).days > EVENT_GRACE_DAYS:
            continue
        e["held_since"] = e["last_seen"]
        held.append(e)
    return fresh + held, held


def write_cache(sid: str, events: list[dict]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{sid}.json").write_text(json.dumps({
        "at": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }, indent=1))


def read_cache(sid: str) -> tuple[list[dict], int | None]:
    path = CACHE / f"{sid}.json"
    if not path.exists():
        return [], None
    blob = json.loads(path.read_text())
    then = datetime.fromisoformat(blob["at"])
    age = (datetime.now(timezone.utc) - then).days
    events = [e for e in blob["events"]
              if (e.get("start") or "")[:10] >= datetime.now().date().isoformat()]
    for e in events:
        e["from_cache"] = True
    return events, age


# --------------------------------------------------------------------------
# hand-entered and submitted events
# --------------------------------------------------------------------------

def load_manual(home: dict, radius: int) -> list[dict]:
    """
    data/manual.yml — anything the shop adds by hand. Highest trust, so it
    overrides a scraped version of the same event.
    """
    path = ROOT / "data" / "manual.yml"
    if not path.exists():
        return []
    blob = yaml.safe_load(path.read_text()) or {}
    source = {"id": "altar", "name": "Altar Cycles", "trust": 100,
              "default_category": "race", "org_url": "https://altar.bike",
              # The only source allowed to set an event's credit line, because
              # it is the only one a human types by hand. See normalize.prepare.
              "hand_entered": True}
    events = normalize.prepare(blob.get("events", []) or [], source, home, radius)
    print(f"  ok    {'altar (manual)':26} {'yaml':12} {len(events):3} events")
    return events


def load_submissions(home: dict, radius: int) -> list[dict]:
    """
    Community submissions arrive as GitHub issues. Anything labelled
    'approved' gets published; everything else waits for review.
    Set GITHUB_TOKEN and GITHUB_REPOSITORY to enable.
    """
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        return []
    try:
        r = adapters.http(
            f"https://api.github.com/repos/{repo}/issues",
            params={"labels": "approved,event-submission", "state": "open",
                    "per_page": 100},
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"})
        issues = r.json()
    except Exception as exc:                          # noqa: BLE001
        print(f"  warn  submissions: {exc}")
        return []

    parsed = []
    for issue in issues:
        fields = parse_issue_body(issue.get("body") or "")
        if not fields.get("title") or not fields.get("start"):
            continue
        fields["submitted_by"] = (issue.get("user") or {}).get("login", "")
        fields["issue"] = issue.get("html_url")
        parsed.append(fields)

    source = {"id": "community", "name": "Community submission", "trust": 90,
              "default_category": "group-ride", "org_url": "https://altar.bike/calendar"}
    events = normalize.prepare(parsed, source, home, radius)
    print(f"  ok    {'community':26} {'issues':12} {len(events):3} approved")
    return events


def parse_issue_body(body: str) -> dict:
    """Reads the `### Field` / value blocks GitHub issue forms produce."""
    keys = {"event name": "title", "date": "start", "end date": "end",
            "start time": "_time", "location": "venue", "city": "city",
            "state": "state", "link": "url", "website": "url",
            "details": "description", "description": "description",
            "cost": "cost", "category": "category_hint",
            "what is it": "category_hint",
            # Recurring events. "Repeats" is a dropdown, "Repeats until" a date.
            "repeats": "repeat", "repeat": "repeat",
            "how often": "repeat", "recurrence": "repeat",
            "repeats until": "repeat_until", "repeat until": "repeat_until",
            "last date": "repeat_until"}
    out, current = {}, None
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("###"):
            current = keys.get(line.lstrip("#").strip().lower())
        elif line and current and line != "_No response_":
            out[current] = (out.get(current, "") + " " + line).strip()
    if out.get("start") and out.get("_time"):
        out["start"] = f"{out['start']} {out.pop('_time')}"
    else:
        out.pop("_time", None)
        out["all_day"] = True
    # The submitter's own answer to "what is it" used to be parsed and then
    # thrown away by a `pass`. It is the best category signal we get — the
    # person running the ride knows whether it is a race — so keep it. It is a
    # *hint*: normalize.classify still gets the final say, because submitters
    # pick "Race" for anything competitive-sounding.
    if out.get("category_hint"):
        out["category_hint"] = out["category_hint"].strip().lower()
    return out


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def to_ics(events: list[dict], name: str, description: str) -> bytes:
    from icalendar import Calendar, Event

    cal = Calendar()
    cal.add("prodid", "-//Altar Cycles//WNC Cycling Calendar//EN")
    cal.add("version", "2.0")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", name)
    cal.add("x-wr-caldesc", description)
    cal.add("x-wr-timezone", "America/New_York")
    cal.add("refresh-interval;value=duration", "PT6H")

    for e in events:
        item = Event()
        item.add("uid", f"{e['uid']}@altar.bike")
        # A month-precision event is anchored to the 1st purely so it has a
        # DTSTART. Saying so in the SUMMARY is the whole point: a subscriber
        # sees "Tour de Fat (date TBA)" sitting on 1 October and knows not to
        # plan around that square, instead of seeing a confident wrong day.
        month_only = e.get("date_precision") == normalize.MONTH_PRECISION
        item.add("summary", f"{e['title']} (date TBA)" if month_only else e["title"])
        try:
            start = datetime.fromisoformat(e["start"])
        except (ValueError, KeyError):
            continue
        item.add("dtstart", start.date() if e.get("all_day") else start)
        if e.get("end"):
            try:
                end = datetime.fromisoformat(e["end"])
                item.add("dtend", end.date() if e.get("all_day") else end)
            except ValueError:
                pass
        item.add("dtstamp", datetime.now(timezone.utc))

        where = ", ".join(filter(None, [e.get("venue"), e.get("city"), e.get("state")]))
        if where:
            item.add("location", where)
        if e.get("lat") and e.get("lng"):
            item.add("geo", (e["lat"], e["lng"]))
        if e.get("url"):
            item.add("url", e["url"])

        body = []
        if month_only:
            month = datetime.fromisoformat(e["start"]).strftime("%B %Y")
            body.append(f"Date not yet announced. {e['source_name']} lists this "
                        f"as a {month} event; the day above is a placeholder.")
        body.append(e.get("description", ""))
        if e.get("register_url"):
            body.append(f"Register: {e['register_url']}")
        elif e.get("url"):
            body.append(f"More: {e['url']}")
        body.append(f"Source: {e['source_name']}")
        body.append("Listed by Altar Cycles — altar.bike/calendar")
        item.add("description", "\n\n".join(filter(None, body)))
        item.add("categories", [e.get("category", "event")])
        cal.add_component(item)

    return cal.to_ical()


def emit(events: list[dict], report: dict, site: Path | None = None) -> None:
    site = Path(site) if site else SITE
    site.mkdir(parents=True, exist_ok=True)
    local = [e for e in events if e.get("bucket") != "world"]
    world = [e for e in events if e.get("bucket") == "world"]

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "count": len(local),
        "events": local,
        "world": world,
        "sources": sorted({e["source_name"] for e in local}),
        "categories": sorted({e["category"] for e in local}),
    }
    (site / "events.json").write_text(json.dumps(payload, indent=1))
    (site / "events.ics").write_bytes(
        to_ics(local, "WNC Cycling — by Altar Cycles",
               "Races, group rides, trail work and festivals within about "
               "75 miles of Asheville. Maintained by Altar Cycles. altar.bike"))
    # Filenames here must match the links in site/index.html.
    for cat, filename, label in (("race", "races.ics", "WNC Bike Racing"),
                                 ("trail-work", "trail-work.ics", "WNC Trail Work Days")):
        subset = [e for e in local if e["category"] == cat]
        (site / filename).write_bytes(
            to_ics(subset, f"{label} — Altar Cycles", f"{label}. altar.bike"))

    stale = [s for s, r in report.items()
             if r.get("status") not in ("ok", "off-season") and not r.get("optional")]
    (site / "build-report.json").write_text(json.dumps({
        "generated": payload["generated"],
        "total": len(local),
        "world": len(world),
        "needs_attention": stale,
        "sources": report,
    }, indent=1))

    print(f"\n  {len(local)} local events, {len(world)} world")
    print(f"  by category: " + ", ".join(
        f"{c}={sum(1 for e in local if e['category'] == c)}"
        for c in payload["categories"]))
    if stale:
        print(f"\n  needs attention: {', '.join(stale)}")


# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run a single source id")
    ap.add_argument("--offline", action="store_true", help="rebuild from cache only")
    ap.add_argument("--config", default="sources.yml")
    args = ap.parse_args()

    config = yaml.safe_load((ROOT / args.config).read_text())
    home = config["defaults"]["home"]
    radius = config["defaults"]["radius_miles"]

    print(f"Altar events build — {datetime.now():%Y-%m-%d %H:%M}\n")

    collected: list[dict] = []
    report: dict = {}

    for source in config["sources"]:
        if args.only and source["id"] != args.only:
            continue
        if args.offline:
            cached, age = read_cache(source["id"])
            report[source["id"]] = {"status": "cached", "cache_age_days": age,
                                    "cached_events": len(cached)}
            collected += cached
            continue
        collected += fetch_source(source, home, radius, report)

    if not args.only:
        # Everywhere else the rule is "one dead source never kills the build" —
        # fetch_source catches, logs, and serves cache. These two were the
        # exception, and data/manual.yml is the ONE file a non-programmer edits
        # by hand. A single mistyped date in it took down the entire publish,
        # every other source with it. Found 2026-08-24 while adding a standing
        # ride. They now fail like any other source: loudly, in the report, and
        # alone.
        loaders = [("altar", load_manual)]
        if not args.offline:
            loaders.append(("community", load_submissions))
        for sid, loader in loaders:
            try:
                collected += loader(home, radius)
            except Exception as exc:                  # noqa: BLE001
                report[sid] = {"status": "failed", "errors": [str(exc)],
                               "cached_events": 0, "cache_age_days": None,
                               "optional": False}
                print(f"  FAIL  {sid:26} {str(exc)[:70]}")

    before = len(collected)
    merged = normalize.dedupe(collected)
    print(f"\n  deduped {before} -> {len(merged)}")

    emit(merged, report)

    hard_failures = [s for s, r in report.items()
                     if r["status"] == "failed" and not r.get("optional")]
    if hard_failures:
        print(f"\n  {len(hard_failures)} source(s) failed with no cache: "
              f"{', '.join(hard_failures)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
