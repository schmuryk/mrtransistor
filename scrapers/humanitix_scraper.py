#!/usr/bin/env python3
"""
Humanitix scraper — fetches events from known Humanitix organiser pages on
Vancouver Island and merges them into projects/live-shows/events.json.

No API key required. Uses the SvelteKit __data.json endpoint each host page
exposes, which returns all upcoming events in a structured indexed format.

To add a new host: find the organiser's Humanitix slug from their URL
(https://events.humanitix.com/host/<slug>) and add it to HOSTS.

Known hosts:
  charslanding  — Char's Landing, Port Alberni (port-alberni)

Usage:
    python3 scrapers/humanitix_scraper.py             # dry-run
    python3 scrapers/humanitix_scraper.py --write     # merge
    python3 scrapers/humanitix_scraper.py --replace   # re-sync
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EVENTS_JSON = Path(__file__).parent.parent / "projects" / "live-shows" / "events.json"
SOURCE_ID   = "humanitix"
API_TEMPLATE = "https://events.humanitix.com/host/{slug}/__data.json"

# (slug, default_city) — city is used when the event location doesn't specify one
HOSTS = [
    ("charslanding", "Port Alberni"),
]

CITY_FIXES = {
    "victoria":           "Victoria",
    "saanich":            "Victoria",
    "langford":           "Victoria",
    "nanaimo":            "Nanaimo",
    "ladysmith":          "Ladysmith",
    "chemainus":          "Chemainus",
    "duncan":             "Duncan",
    "lake cowichan":      "Lake Cowichan",
    "cowichan bay":       "Cowichan Bay",
    "port alberni":       "Port Alberni",
    "tofino":             "Tofino",
    "ucluelet":           "Ucluelet",
    "parksville":         "Parksville",
    "qualicum beach":     "Qualicum Beach",
    "courtenay":          "Comox Valley",
    "comox":              "Comox Valley",
    "cumberland":         "Comox Valley",
    "campbell river":     "Campbell River",
    "port mcneill":       "Port McNeill",
    "port hardy":         "Port Hardy",
    "salt spring island": "Salt Spring Island",
    "salt spring":        "Salt Spring Island",
    "ganges":             "Salt Spring Island",
    "pender island":      "Pender Island",
    "galiano island":     "Galiano Island",
    "gibsons":            "Gibsons",
    "sechelt":            "Sechelt",
    "powell river":       "Powell River",
}

NON_MUSIC_RE = re.compile(
    r'\b(meat draw|easter|egg hunt|bingo|trivia|yoga|meditation|workshop|'
    r'art class|art show|gallery|exhibit|craft fair|market|sale|'
    r'fundraiser dinner|gala dinner|luncheon|brunch|paint night|pottery|'
    r'knitting|sewing|weaving|quilting|book launch|book club|library|'
    r'film night|movie night|cinema|fitness|seated fitness|'
    r'story time|storytime|playtime|scavenger hunt)\b',
    re.I,
)


def normalise_city(raw: str) -> str:
    key = (raw or "").strip().lower()
    return CITY_FIXES.get(key, (raw or "").strip().title())


# ── Fetch & parse SvelteKit __data.json ─────────────────────────────────────────

def fetch_host(slug: str) -> list[dict]:
    """Fetch and decode the SvelteKit __data.json for a Humanitix host page."""
    url = API_TEMPLATE.format(slug=slug)
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} for host '{slug}': {e.reason}", file=sys.stderr)
        return []
    except urllib.error.URLError as e:
        print(f"  Network error for host '{slug}': {e.reason}", file=sys.stderr)
        return []

    # Find the data node with the events array (it's the largest one)
    nodes = raw.get("nodes", [])
    data_node = next(
        (n for n in nodes if isinstance(n.get("data"), list) and len(n["data"]) > 50),
        None,
    )
    if not data_node:
        print(f"  No data node found for host '{slug}'", file=sys.stderr)
        return []

    data = data_node["data"]

    def resolve(idx: int):
        """Recursively resolve SvelteKit's indexed data format."""
        if idx == -1:
            return None
        v = data[idx]
        if isinstance(v, dict):
            return {k: resolve(vi) for k, vi in v.items()}
        if isinstance(v, list):
            return [resolve(i) for i in v]
        return v

    try:
        top = resolve(0)
    except (IndexError, TypeError) as e:
        print(f"  Parse error for host '{slug}': {e}", file=sys.stderr)
        return []

    return top.get("events") or []


# ── Convert ─────────────────────────────────────────────────────────────────────

def parse_datetime(iso: str) -> tuple[str | None, str | None]:
    """'2026-04-09T19:30:00-0700' → ('2026-04-09', '19:30')"""
    if not iso:
        return None, None
    date_part = iso[:10]
    time_part = iso[11:16] if len(iso) >= 16 else None
    return date_part, time_part


def to_live_shows_event(raw: dict, default_city: str) -> dict | None:
    title = html.unescape((raw.get("title") or "")).strip()
    if not title:
        return None

    if NON_MUSIC_RE.search(title):
        return None

    loc       = raw.get("location") or {}
    city_raw  = (loc.get("locality") or "").strip()
    country   = (loc.get("country") or "").lower()

    # Only keep Canadian events
    if country and country not in ("canada", "ca"):
        return None

    city = normalise_city(city_raw) if city_raw else default_city

    dates    = raw.get("dates") or {}
    iso_dt   = dates.get("timeTagDate") or ""
    date_part, time_part = parse_datetime(iso_dt)

    # Image from banner
    images   = raw.get("images") or {}
    banner   = images.get("banner") or {}
    image_url = banner.get("src") or None

    # Ticket URL
    urls      = raw.get("urls") or {}
    ticket_url = urls.get("url") or None

    # Venue name
    venue_name = (loc.get("venueName") or loc.get("displayLocationPrimary") or "").strip() or None

    # ID from slug (stable)
    slug = raw.get("slug") or raw.get("id") or ""

    return {
        "id":         f"{SOURCE_ID}:{slug}",
        "title":      title,
        "venue":      venue_name,
        "city":       city,
        "date":       date_part,
        "start_time": time_part,
        "ticket_url": ticket_url,
        "price":      None,   # price not available in host listing view
        "image_url":  image_url,
        "genre":      None,
    }


# ── Merge ───────────────────────────────────────────────────────────────────────

def load_events_json() -> dict:
    if EVENTS_JSON.exists():
        with EVENTS_JSON.open(encoding="utf-8") as f:
            return json.load(f)
    return {"generated": "", "events": []}


def save_events_json(data: dict) -> None:
    with EVENTS_JSON.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def merge(existing: list[dict], incoming: list[dict], replace: bool) -> tuple[list[dict], int, int]:
    if replace:
        kept     = [e for e in existing if not e["id"].startswith(f"{SOURCE_ID}:")]
        old_ids  = {e["id"] for e in existing if e["id"].startswith(f"{SOURCE_ID}:")}
        new_ids  = {e["id"] for e in incoming}
        replaced = len(old_ids & new_ids)
        added    = len(new_ids - old_ids)
        merged   = kept + incoming
    else:
        existing_ids = {e["id"] for e in existing}
        new_events   = [e for e in incoming if e["id"] not in existing_ids]
        added, replaced = len(new_events), 0
        merged = existing + new_events

    merged.sort(key=lambda e: e.get("date") or "")
    return merged, added, replaced


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Humanitix scraper → events.json")
    parser.add_argument("--write",   action="store_true", help="write results to events.json")
    parser.add_argument("--replace", action="store_true", help="replace all humanitix events (implies --write)")
    args = parser.parse_args()

    if args.replace:
        args.write = True

    today     = datetime.now().strftime("%Y-%m-%d")
    all_raw:  list[dict] = []
    seen_ids: set[str]   = set()

    for slug, default_city in HOSTS:
        print(f"Fetching Humanitix host: {slug} (default city: {default_city}) …", file=sys.stderr)
        events = fetch_host(slug)
        new_here = 0
        for e in events:
            eid = e.get("slug") or e.get("id") or ""
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                all_raw.append((e, default_city))
                new_here += 1
        print(f"  {len(events)} events fetched, {new_here} unique.", file=sys.stderr)
        time.sleep(0.5)

    print(f"\n{len(all_raw)} unique raw events. Converting …", file=sys.stderr)
    incoming = []
    for raw, default_city in all_raw:
        # Filter to upcoming events only
        dates   = raw.get("dates") or {}
        iso_dt  = dates.get("timeTagDate") or ""
        if iso_dt[:10] < today:
            continue
        ev = to_live_shows_event(raw, default_city)
        if ev and ev["title"] and ev["date"]:
            incoming.append(ev)
    print(f"{len(incoming)} kept after filtering.", file=sys.stderr)

    data     = load_events_json()
    existing = data.get("events", [])
    print(f"{len(existing)} events currently in events.json.", file=sys.stderr)

    merged, added, replaced = merge(existing, incoming, replace=args.replace)

    if args.write:
        data["events"]    = merged
        data["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        save_events_json(data)
        print(f"Written: {added} added, {replaced} replaced. Total: {len(merged)}.", file=sys.stderr)
    else:
        existing_ids = {e["id"] for e in existing}
        new_events   = [e for e in incoming if e["id"] not in existing_ids]
        if not new_events:
            print("No new events to add.", file=sys.stderr)
        else:
            print(f"\n{len(new_events)} new event(s) would be added (dry-run, use --write):\n", file=sys.stderr)
            for e in new_events[:60]:
                print(f"  {e['date']}  {(e['city'] or ''):<20}  {(e['title'] or '')[:60]}")
            if len(new_events) > 60:
                print(f"  … and {len(new_events) - 60} more")


if __name__ == "__main__":
    main()
