#!/usr/bin/env python3
"""
Find out how a site publishes its events, before you write it into sources.yml.

    python probe.py https://someclub.org/events
    python probe.py --check          # re-test every source already configured

Tries every known feed shape and prints what came back. Use it when adding a
new source, and when an existing one starts failing — sites move their feeds
and this tells you where it went in about ten seconds.
"""

from __future__ import annotations

import argparse
import re
import sys
from urllib.parse import urljoin, urlparse

import yaml
from bs4 import BeautifulSoup

import adapters


def probe_url(url: str) -> None:
    print(f"\n{url}\n" + "=" * min(len(url), 72))
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    # What is this site even built on? Tells you which adapter to reach for.
    try:
        html = adapters.http(url).text
    except adapters.SourceError as exc:
        print(f"  page unreachable: {exc}")
        return

    signatures = {
        "WordPress + The Events Calendar": ("tribe-events", "tribe_events"),
        "WordPress": ("wp-content", "wp-json"),
        "Squarespace": ("squarespace", "sqs-block", "static1.squarespace"),
        "Wix": ("wix.com", "wixstatic", "_partials/wix"),
        "GoDaddy Website Builder": ("Go Daddy Website Builder", "img1.wsimg.com"),
        "ClubExpress": ("clubexpress", "content.aspx?page_id"),
        "Webflow": ("webflow",),
        "Ride with GPS": ("ridewithgps",),
    }
    hits = [name for name, needles in signatures.items()
            if any(n.lower() in html.lower() for n in needles)]
    print(f"  platform : {', '.join(hits) if hits else 'unknown'}")

    # Declared feeds in <link rel>.
    soup = BeautifulSoup(html, "html.parser")
    feeds = []
    for link in soup.find_all("link", rel=True):
        rel = " ".join(link.get("rel", [])).lower()
        href = link.get("href", "")
        if "alternate" in rel and any(t in (link.get("type") or "")
                                      for t in ("rss", "atom", "xml", "calendar")):
            feeds.append(urljoin(base, href))
    for a in soup.find_all("a", href=True):
        if re.search(r"\.ics(\?|$)|format=ical|ical=1|/feed/?$", a["href"], re.I):
            feeds.append(urljoin(url, a["href"]))
    if feeds:
        print("  declared :")
        for f in sorted(set(feeds))[:8]:
            print(f"             {f}")

    # schema.org Events sitting in the page already.
    try:
        found = adapters.jsonld({"url": url})
        print(f"  jsonld   : {len(found)} events  <- use adapter: jsonld")
        for e in found[:2]:
            print(f"             {e['start'][:10]}  {e['title'][:52]}")
    except adapters.SourceError:
        print("  jsonld   : none")

    # Endpoints worth guessing, in the order they're most likely to work.
    candidates = [
        ("tribe", urljoin(base, "/wp-json/tribe/events/v1/events")),
        ("squarespace", url),
        ("ics", url.rstrip("/") + "?format=ical"),
        ("ics", url.rstrip("/") + "?ical=1"),
        ("ics", urljoin(base, "/events.ics")),
        ("ics", urljoin(base, "/calendar.ics")),
        ("rss", url.rstrip("/") + "/feed/"),
    ]
    for adapter, candidate in candidates:
        try:
            got = adapters.REGISTRY[adapter]({"url": candidate})
            if got:
                print(f"  {adapter:9}: {len(got):3} events  <- adapter: {adapter}")
                print(f"             url: {candidate}")
                print(f"             e.g. {got[0]['start'][:10]}  {got[0]['title'][:48]}")
        except Exception:                                  # noqa: BLE001
            pass

    print("  llm      : always works as a fallback (needs ANTHROPIC_API_KEY)")


def check_configured(path: str) -> None:
    config = yaml.safe_load(open(path))
    home = config["defaults"]["home"]
    radius = config["defaults"]["radius_miles"]
    print(f"Re-testing {len(config['sources'])} configured sources\n")
    broken = []
    for source in config["sources"]:
        attempts = [source] + [{**source, **fb} for fb in source.get("fallback", [])]
        for attempt in attempts:
            try:
                fn = adapters.REGISTRY[attempt["adapter"]]
                got = (fn(attempt, home, attempt.get("radius_miles", radius))
                       if attempt["adapter"] in adapters.NEEDS_GEO else fn(attempt))
                if got:
                    tag = "" if attempt is source else f" (via fallback {attempt['adapter']})"
                    print(f"  ok    {source['id']:26} {len(got):3} events{tag}")
                    break
            except Exception as exc:                       # noqa: BLE001
                last = exc
        else:
            print(f"  FAIL  {source['id']:26} {str(last)[:60]}")
            broken.append(source["id"])
    if broken:
        print(f"\n{len(broken)} broken: {', '.join(broken)}")
        print("Run `python probe.py <their events page>` to find the new feed.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?")
    ap.add_argument("--check", action="store_true", help="re-test sources.yml")
    ap.add_argument("--config", default="sources.yml")
    args = ap.parse_args()

    if args.check:
        check_configured(args.config)
    elif args.url:
        probe_url(args.url)
    else:
        ap.print_help()
        sys.exit(1)
