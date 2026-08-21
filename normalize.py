"""
Turn a pile of raw events from nine different sources into one clean calendar.

Order matters here:
  1. drop junk           — no date, past, blocked title
  2. classify            — race / trail-work / group-ride / ...
  3. locate              — geofence to the region we actually ride in
  4. cap                 — stop high-volume clubs from swamping the calendar
  5. dedupe              — one event, even when four sources report it
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
from difflib import SequenceMatcher

# --------------------------------------------------------------------------
# classification
# --------------------------------------------------------------------------
# Ordered: the first rule that matches wins, so put the specific ones on top.
# "trail work" has to beat "race" because a lot of dig days are titled
# "Trail Work Day before the Old Fort Fifty".

#
# Matching is on WORD BOUNDARIES, not substrings. That distinction is load
# bearing: a bare "camp" filed the Old Fort Fifty (start/finish at Camp Grier)
# as a clinic, and a bare "class" matches inside "classic". Only add phrases
# that appear exclusively in the sense you mean.

CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("trail-work", ("trail work", "work day", "workday", "dig day", "dig night",
                    "dig evening", "trail day", "trail weekend", "volunteer",
                    "trail crew", "brushing", "chainsaw", "stewardship",
                    "adopt a trail", "public lands day")),
    ("youth",      ("nica", "high school", "middle school", "junior", "juniors",
                    "youth", "grit ride", "composite team", "interscholastic")),
    ("clinic",     ("clinic", "clinics", "skills session", "skills clinic",
                    "smart cycling", "workshop", "learn to ride", "learn to",
                    "boot camp", "skills camp", "cycling camp", "training camp",
                    "coaching", "maintenance class", "intro to")),
    ("advocacy",   ("advocacy", "town hall", "public comment", "forum",
                    "planning meeting", "board meeting", "annual meeting")),
    ("festival",   ("festival", "fest", "expo", "demo day", "bike love",
                    "summer cycle", "gear show", "bike show", "swap",
                    "premiere", "film", "party", "gala", "celebration",
                    "ribbon cutting", "open house")),
    ("race",       ("race", "races", "racing", "criterium", "crit", "cyclocross",
                    "cx", "time trial", "tt", "enduro", "downhill", "dh", "xc",
                    "grand prix", "stage race", "gran fondo", "fondo", "grinder",
                    "gravel grind", "hill climb", "hctt", "world cup",
                    "championship", "championships", "omnium", "short track",
                    "dual slalom")),
    ("group-ride", ("group ride", "shop ride", "social ride", "community ride",
                    "shakeout", "no drop", "no-drop", "ride out", "roll out",
                    "coffee ride", "ladies ride", "women's ride", "night ride",
                    "recovery ride")),
]

DISCIPLINE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("gravel",   ("gravel", "grinder", "dirt road", "grit", "diggler")),
    ("mtb",      ("mtb", "mountain bike", "singletrack", "enduro", "downhill",
                  "xc", "cross country", "trail", "trails", "pisgah", "dupont",
                  "bent creek", "old fort", "kitsuma", "heartbreak", "gateway")),
    ("cx",       ("cyclocross", "cx", "grand prix")),
    ("road",     ("road", "criterium", "crit", "century", "gran fondo", "fondo",
                  "time trial", "parkway", "metric", "peloton")),
    ("commuter", ("commute", "bike to work", "urban", "greenway", "valet")),
]


def _compile(rules):
    """One alternation per category, so matching is a single regex pass."""
    out = []
    for name, phrases in rules:
        alt = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
        out.append((name, re.compile(rf"(?<!\w)(?:{alt})(?!\w)", re.I)))
    return out


_CATEGORY = _compile(CATEGORY_RULES)

# The categories the submission form and issue template are allowed to assert.
# Kept in sync with the <select> in site/submit.html.
CATEGORIES = {name for name, _ in CATEGORY_RULES} | {
    "race", "group-ride", "trail-work", "festival", "clinic", "youth",
    "advocacy", "watch"}
_DISCIPLINE = _compile(DISCIPLINE_RULES)


def _haystack(event: dict) -> str:
    return f" {event.get('title', '')} {(event.get('description') or '')[:300]} "


def classify(event: dict, default: str) -> tuple[str, str]:
    """Returns (category, basis) where basis is 'rule' or 'default'.

    The basis matters at merge time. A source-level default is a blunt
    instrument — G5 defaults to trail-work because most of what they post is
    dig days, but they also promote the Old Fort Fifty. When two sources
    describe one event, an explicit keyword match should win over anybody's
    default.
    """
    hay = _haystack(event)
    for category, pattern in _CATEGORY:
        if pattern.search(hay):
            return category, "rule"
    # No keyword matched. Before falling back to the source's blunt default,
    # use the submitter's own answer to "what is it" if there is one — the
    # person running the ride knows what it is better than our default does.
    # Ranked below the keyword rules on purpose: submitters over-pick "Race".
    hint = str(event.get("category_hint") or "").strip().lower()
    if hint in CATEGORIES:
        return hint, "rule"
    return default, "default"


def discipline(event: dict) -> str:
    hint = event.get("discipline_hint") or ""
    if hint:
        for name, pattern in _DISCIPLINE:
            if pattern.search(f" {hint} "):
                return name
    hay = _haystack(event)
    for name, pattern in _DISCIPLINE:
        if pattern.search(hay):
            return name
    return "other"


# --------------------------------------------------------------------------
# geography
# --------------------------------------------------------------------------

# Towns we consider "ours" even when a source gives no coordinates.
REGION_TOWNS = {
    "asheville", "black mountain", "brevard", "pisgah forest", "old fort",
    "hendersonville", "fletcher", "mills river", "candler", "weaverville",
    "swannanoa", "marshall", "mars hill", "burnsville", "spruce pine",
    "waynesville", "canton", "sylva", "cullowhee", "bryson city", "cherokee",
    "marion", "morganton", "lenoir", "boone", "blowing rock", "banner elk",
    "tryon", "columbus", "mill spring", "saluda", "flat rock", "etowah",
    "zirconia", "hot springs", "leicester", "arden", "fairview", "barnardsville",
    "greenville", "travelers rest", "landrum", "erwin", "johnson city",
    "kingsport", "hartford", "del rio", "newport",
}
REGION_STATES = {"NC", "SC", "TN", "GA", "VA"}


def haversine(lat1, lng1, lat2, lng2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def in_region(event: dict, home: dict, radius: int) -> bool:
    """Coordinates win, then a server-side radius we trust, then the town list."""
    lat, lng = event.get("lat"), event.get("lng")
    if lat is not None and lng is not None and (lat or lng):
        event["distance_mi"] = round(haversine(home["lat"], home["lng"], lat, lng), 1)
        return event["distance_mi"] <= radius

    # Some platforms filter by radius server-side but return no coordinates at
    # all — RunSignup is the confirmed case (see adapters.runsignup). For those
    # the town list is actively harmful: it would drop Statesville and
    # Morristown races the server already told us are inside our radius. An
    # adapter sets this flag ONLY when it passed home + radius to the API and
    # verified the API honours them.
    if event.get("pre_geofenced"):
        return True

    city = (event.get("city") or "").strip().lower()
    if city in REGION_TOWNS:
        return True

    state = (event.get("state") or "").strip().upper()
    blob = f"{event.get('venue','')} {event.get('city','')} {event.get('description','')[:200]}".lower()
    if any(town in blob for town in REGION_TOWNS):
        return True
    # No location signal at all: trust the org. A local club posting an event
    # without an address is almost always local.
    return not city and not state


# --------------------------------------------------------------------------
# dedupe
# --------------------------------------------------------------------------

_NOISE = re.compile(
    r"\b(20\d\d|the|a|an|presented by|p/b|pb|presents|annual|\d+(st|nd|rd|th)|"
    r"race|event|series|round|rd)\b")


_SHOUT_LEAD = re.compile(
    r"^\s*(?:sold\s*out|new|updated|announcing|register\s+now|last\s+chance|"
    r"final\s+call|don'?t\s+miss(?:\s+out)?|hurry|act\s+fast)\b[\s!.:;,–—-]*",
    re.I)
_DASH_RUN = re.compile(r"[–—-]{2,}")


def tidy_title(title: str) -> str:
    """
    Take the shouting out of promoter-supplied titles.

    Registration platforms pass through whatever the promoter typed, and some
    of it is marketing rather than a name. Live example from the calendar on
    launch day:

        "SOLD OUT !!! ———12TH ANNUAL DANCING BEAR BIKE BASH RETURNS ON
         SEPTEMBER 19TH, 2026"

    Strips a leading marketing shout, collapses runs of dashes and bangs, and
    de-shouts an all-caps title. Deliberately conservative: it only lowercases
    when the title is *entirely* caps, so "NCCX", "UCI" and "WNC Flyer" are
    left alone, and it never touches the middle of a title.
    """
    t = re.sub(r"\s+", " ", str(title or "")).strip()
    if not t:
        return t
    prev = None
    while prev != t:                       # "SOLD OUT !!! NEW! ..." — peel each
        prev = t
        t = _SHOUT_LEAD.sub("", t).strip()
    t = _DASH_RUN.sub(" — ", t)
    t = re.sub(r"!{2,}", "!", t)
    t = re.sub(r"\s+", " ", t).strip(" -–—:;,")

    letters = [c for c in t if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) > 6:
        t = t.title()
        # title() mangles the common ordinals and small words; put them back.
        t = re.sub(r"\b(\d+)(St|Nd|Rd|Th)\b", lambda m: m.group(1) + m.group(2).lower(), t)
        t = re.sub(r"\b(And|Of|The|On|At|For|To|In|A|An)\b",
                   lambda m: m.group(1).lower(), t)
        t = t[0].upper() + t[1:] if t else t
    return t


def slug(title: str) -> str:
    t = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = _NOISE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def same_event(a: dict, b: dict) -> bool:
    """Same day (or overlapping range) plus a near-identical title."""
    try:
        da = datetime.fromisoformat(a["start"]).date()
        db = datetime.fromisoformat(b["start"]).date()
    except (ValueError, KeyError):
        return False
    if abs((da - db).days) > 1:
        return False

    sa, sb = slug(a["title"]), slug(b["title"])
    if not sa or not sb:
        return False
    if sa == sb or sa in sb or sb in sa:
        return True
    return SequenceMatcher(None, sa, sb).ratio() >= 0.87


def merge(winner: dict, loser: dict) -> dict:
    """Keep the trusted record but backfill anything it's missing."""
    for field in ("description", "venue", "city", "state", "lat", "lng",
                  "image", "cost", "end", "distance"):
        if not winner.get(field) and loser.get(field):
            winner[field] = loser[field]
    if (winner.get("category_basis") == "default"
            and loser.get("category_basis") == "rule"):
        winner["category"] = loser["category"]
        winner["category_basis"] = "rule"

    seen = winner.setdefault("also_listed_by", [])
    if loser["source_name"] not in seen and loser["source_name"] != winner["source_name"]:
        seen.append(loser["source_name"])
    # A registration link is more useful than an org homepage.
    if "reg" in (loser.get("url") or "") and "reg" not in (winner.get("url") or ""):
        winner["register_url"] = loser["url"]
    return winner


def dedupe(events: list[dict]) -> list[dict]:
    """Bucket by month so this stays O(n) in practice, then compare in-bucket."""
    events.sort(key=lambda e: (-e.get("trust", 0), e.get("start", "")))
    buckets: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        key = (e.get("start") or "")[:7]
        for kept in buckets[key]:
            if same_event(kept, e):
                merge(kept, e)
                break
        else:
            buckets[key].append(e)
    out = [e for bucket in buckets.values() for e in bucket]
    out.sort(key=lambda e: e.get("start", ""))
    return out


# --------------------------------------------------------------------------
# volume control
# --------------------------------------------------------------------------

def cap_weekly(events: list[dict], per_week: int, keep_matching: list[str]) -> list[dict]:
    """
    A club running nightly rides shouldn't own the calendar. Keep the flagged
    events plus the first `per_week` others in each ISO week.
    """
    keepers, counts = [], defaultdict(int)
    for e in sorted(events, key=lambda x: x.get("start", "")):
        title = e.get("title", "").lower()
        if any(k in title for k in keep_matching):
            keepers.append(e)
            continue
        try:
            week = datetime.fromisoformat(e["start"]).isocalendar()[:2]
        except (ValueError, KeyError):
            keepers.append(e)
            continue
        if counts[week] < per_week:
            counts[week] += 1
            e["capped_series"] = True
            keepers.append(e)
    return keepers


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------

def uid(event: dict) -> str:
    basis = f"{slug(event.get('title',''))}|{(event.get('start') or '')[:10]}"
    return hashlib.sha1(basis.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# recurrence
# --------------------------------------------------------------------------

# What people actually type in a form field, mapped to a step in days.
# Monthly is handled separately because 4 weeks is not a month.
_REPEAT_DAYS = {
    "weekly": 7, "every week": 7, "each week": 7, "1 week": 7, "7 days": 7,
    "biweekly": 14, "bi-weekly": 14, "fortnightly": 14, "every two weeks": 14,
    "every other week": 14, "2 weeks": 14,
}
_REPEAT_MONTHLY = {"monthly", "every month", "each month", "1 month"}
_REPEAT_NONE = {"", "none", "no", "once", "one-off", "one off", "single",
                "just once", "n/a", "-"}

# A weekly ride with no end date would otherwise publish ~57 entries across the
# 400-day horizon and swamp everything else. Cap it, and make the shop
# re-submit — a "weekly forever" claim goes stale faster than anyone updates it.
RECUR_DEFAULT_COUNT = 12
RECUR_MAX_COUNT = 60


def _add_months(d, n):
    """Same day-of-month n months on, clamped to the month's length."""
    month = d.month - 1 + n
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0)
                      else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return d.replace(year=year, month=month, day=day)


# --------------------------------------------------------------------------
# long spans: seasons and series masquerading as one event
# --------------------------------------------------------------------------

# More than a long weekend is not one event. Found 2026-08-17 when building the
# month-grid view, which is what made this visible — in an agenda list a long
# span is one harmless row, but in a calendar grid it paints every cell it
# covers. The live data had four:
#   Pisgah Rage Regular Season        180 days  (a season, not an event)
#   Bear's Smokehouse Community Rides 122 days  (a ride series)
#   Pisgah Rage Pre-season             77 days  (a season)
#   Tuesday Night Cyclocross Series    14 days  (a weekly series)
LONG_SPAN_DAYS = 7

_WEEKDAY_RE = re.compile(
    r"\b(mon|tues?|wednes|thurs?|fri|satur|sun)day\b", re.I)
_WEEKDAY_INDEX = {"mon": 0, "tue": 1, "tues": 1, "wednes": 2, "thur": 3,
                  "thurs": 3, "fri": 4, "satur": 5, "sun": 6}


def _named_weekday(title: str):
    """The weekday a title names, e.g. 'Tuesday Night Cyclocross' -> 1."""
    m = _WEEKDAY_RE.search(title or "")
    return _WEEKDAY_INDEX.get(m.group(1).lower()) if m else None


def split_long_span(event: dict) -> list[dict]:
    """
    Break up an event whose `end` is implausibly far from its `start`.

    Deliberately conservative about inventing dates. We only generate
    occurrences when the title NAMES A WEEKDAY — "2026 Tuesday Night
    Cyclocross Training Series" is unambiguously Tuesdays, so Sep 15 -> Sep 29
    becomes three real Tuesday events. Everything else (a "Regular Season", a
    "Community Rides" banner with no cadence stated) keeps its start date and
    records the true range in the description instead. Guessing a weekly cadence
    for those would put invented dates on a customer-facing calendar, which is
    worse than one honest entry.
    """
    try:
        start = datetime.fromisoformat(str(event["start"]))
        end = datetime.fromisoformat(str(event["end"]))
    except (ValueError, KeyError, TypeError):
        return [event]
    span = (end - start).days
    if span <= LONG_SPAN_DAYS:
        return [event]

    fmt = lambda d: d.strftime("%-d %b %Y") if hasattr(d, "strftime") else str(d)
    weekday = _named_weekday(event.get("title", ""))

    if weekday is not None:
        out, when, n = [], start, 0
        while when <= end and n < RECUR_MAX_COUNT:
            if when.weekday() == weekday:
                copy = dict(event)
                copy["start"] = (event["start"] if when == start
                                 else when.isoformat())
                copy.pop("end", None)
                copy["recurring"] = "weekly"
                out.append(copy)
                n += 1
            when += timedelta(days=1)
        if out:
            return out

    # No cadence we can justify. Keep one entry, say what the range is.
    copy = dict(event)
    copy.pop("end", None)
    copy["long_span_days"] = span
    note = f"Runs {fmt(start)} to {fmt(end)}."
    desc = (copy.get("description") or "").strip()
    copy["description"] = f"{note} {desc}".strip() if note not in desc else desc
    return [copy]


def expand_recurrence(event: dict, horizon_days: int = 400) -> list[dict]:
    """
    Turn one event carrying `repeat` into the individual dated events it means.

    Reads `repeat` (weekly / biweekly / monthly) and optional `repeat_until`
    (YYYY-MM-DD). Anything without a recognised `repeat` comes back unchanged,
    so this is safe to call on every event from every source.

    Each occurrence keeps the original's time-of-day and duration. The first
    occurrence keeps the original's exact `start` string so a one-off and the
    first of a series produce the same uid, which means a series can be
    corrected later by a hand-entered event on the same title and date.
    """
    rule = str(event.get("repeat") or "").strip().lower()
    if rule in _REPEAT_NONE:
        return [event]
    step = _REPEAT_DAYS.get(rule)
    monthly = rule in _REPEAT_MONTHLY
    if not step and not monthly:
        return [event]                      # unrecognised: treat as one-off

    try:
        start = datetime.fromisoformat(str(event["start"]))
    except (ValueError, KeyError, TypeError):
        return [event]

    span = None
    if event.get("end"):
        try:
            span = datetime.fromisoformat(str(event["end"])) - start
        except (ValueError, TypeError):
            span = None

    horizon = datetime.now() + timedelta(days=horizon_days)
    until = horizon
    if event.get("repeat_until"):
        try:
            until = min(datetime.fromisoformat(str(event["repeat_until"])[:10]
                                               + "T23:59:59"), horizon)
        except (ValueError, TypeError):
            pass
    limit = RECUR_MAX_COUNT if event.get("repeat_until") else RECUR_DEFAULT_COUNT

    out, when, n = [], start, 0
    while when <= until and n < min(limit, RECUR_MAX_COUNT):
        copy = dict(event)
        copy.pop("repeat", None)
        copy.pop("repeat_until", None)
        copy["start"] = event["start"] if n == 0 else when.isoformat(sep=" " if " " in str(event["start"]) else "T")
        if span is not None:
            copy["end"] = (when + span).isoformat(
                sep=" " if " " in str(event.get("end", "")) else "T")
        copy["recurring"] = rule
        out.append(copy)
        n += 1
        when = _add_months(start, n) if monthly else start + timedelta(days=step * n)
    return out or [event]


def prepare(raw: list[dict], source: dict, home: dict, radius: int) -> list[dict]:
    """Everything that happens to one source's events before the global merge."""
    now = datetime.now()
    floor = (now - timedelta(days=1)).date().isoformat()
    ceiling = (now + timedelta(days=400)).date().isoformat()

    blocked = [b.lower() for b in source.get("drop_if_titled", [])]
    required = [k.lower() for k in source.get("require_keywords", [])]
    is_world = source.get("bucket") == "world"

    # A submitted or hand-entered weekly ride is ONE row that means many dates.
    # Expand before anything else so each occurrence gets its own geofence
    # check, category, uid and horizon test, exactly like a one-off would.
    # Sources that never set `repeat` pass through untouched.
    raw = [x for e in raw for x in split_long_span(e)]
    raw = [occurrence for e in raw for occurrence in expand_recurrence(e)]

    out = []
    for e in raw:
        if not e.get("title") or not e.get("start"):
            continue
        start = e["start"]
        if start[:10] < floor or start[:10] > ceiling:
            continue

        title_l = e["title"].lower()
        if any(b in title_l for b in blocked):
            continue
        if required:
            # By default a keyword may appear in the title OR the description,
            # which is right for an org that writes vague titles. It is WRONG
            # for a general-purpose registration platform: RunSignup is mostly
            # running races, and running-race descriptions mention bikes
            # constantly ("bike valet", "no bikes on course", "packet pickup at
            # the bike shop"). On 2026-08-17 that put 23 events on the live
            # calendar — turkey trots, half marathons, a triathlon, a 5K colour
            # run — and not one genuine bike race. Sources that set
            # `require_in_title` must carry the keyword in the title itself.
            hay = (title_l if source.get("require_in_title")
                   else f"{title_l} {e.get('description','')[:400].lower()}")
            if not any(k in hay for k in required):
                continue

        # Tidy the title AFTER the drop/keyword filters above, so those still
        # match on whatever the promoter actually wrote, and before the uid and
        # dedupe below, so the published title and the dedupe key agree.
        e["title"] = tidy_title(e["title"])
        if not e["title"]:
            continue
        e["source"] = source["id"]
        e["source_name"] = source["name"]
        e["source_url"] = source.get("org_url", "")
        e["trust"] = source.get("trust", 50)
        e["bucket"] = source.get("bucket", "local")
        e["category"], e["category_basis"] = classify(
            e, source.get("default_category", "race"))
        if e["category_basis"] == "default" and source.get("category_authority"):
            # A race-registration platform only ever lists races, so its
            # default is worth as much as a keyword hit.
            e["category_basis"] = "rule"
        e["discipline"] = discipline(e)
        e["uid"] = uid(e)
        e.setdefault("all_day", False)
        e["url"] = e.get("url") or source.get("org_url", "")

        if not is_world and not in_region(e, home, radius):
            continue
        out.append(e)

    if source.get("max_per_week"):
        out = cap_weekly(out, source["max_per_week"], source.get("prefer_titles", []))
    return out
