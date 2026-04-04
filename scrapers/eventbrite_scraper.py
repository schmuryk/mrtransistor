#!/usr/bin/env python3
"""
Eventbrite API v3 scraper — fetches music events on Vancouver Island,
Gulf Islands, and Sunshine Coast, then merges into projects/live-shows/events.json.

Token: create a free account at https://www.eventbrite.com/platform/api
      → My Account → Developer Links → API Keys → private token
Export EB_TOKEN=<your_private_token> before running.

Usage:
    EB_TOKEN=xxx python3 scrapers/eventbrite_scraper.py             # dry-run
    EB_TOKEN=xxx python3 scrapers/eventbrite_scraper.py --write     # merge
    EB_TOKEN=xxx python3 scrapers/eventbrite_scraper.py --replace   # re-sync
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EVENTS_JSON = Path(__file__).parent.parent / "projects" / "live-shows" / "events.json"
SOURCE_ID   = "eventbrite"
API_BASE    = "https://www.eventbriteapi.com/v3/events/search/"
PAGE_SIZE   = 50    # EB max

# Hub points: (label, lat, lon, radius_km)
SEARCH_HUBS = [
    ("Victoria",        48.4284, -123.3656, 35),
    ("Nanaimo",         49.1659, -123.9401, 30),
    ("Courtenay",       49.6893, -124.9944, 30),
    ("Campbell River",  50.0231, -125.2442, 30),
    ("Port Alberni",    49.2337, -124.8022, 25),
    ("Tofino",          49.1534, -125.9064, 30),
    ("Salt Spring Is.", 48.8538, -123.5077, 20),
    ("Powell River",    49.8328, -124.5236, 25),
    ("Parksville",      49.3139, -124.3139, 20),
    ("Port Hardy",      50.7205, -127.4906, 30),
    ("Gibsons",         49.3958, -123.5077, 25),
]

# Music & nightlife category IDs
MUSIC_CATEGORIES = {"103", "104"}   # 103=Music, 104=Nightlife

CITY_FIXES = {
    "victoria":           "Victoria",
    "saanich":            "Victoria",
    "langford":           "Victoria",
    "colwood":            "Victoria",
    "oak bay":            "Victoria",
    "esquimalt":          "Victoria",
    "view royal":         "Victoria",
    "central saanich":    "Victoria",
    "north saanich":      "Victoria",
    "nanaimo":            "Nanaimo",
    "ladysmith":          "Ladysmith",
    "chemainus":          "Chemainus",
    "crofton":            "Crofton",
    "duncan":             "Duncan",
    "cowichan bay":       "Cowichan Bay",
    "lake cowichan":      "Lake Cowichan",
    "port alberni":       "Port Alberni",
    "tofino":             "Tofino",
    "ucluelet":           "Ucluelet",
    "parksville":         "Parksville",
    "qualicum beach":     "Qualicum Beach",
    "qualicum":           "Qualicum Beach",
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
    "mayne island":       "Mayne Island",
    "hornby island":      "Hornby Island",
    "denman island":      "Denman Island",
    "quadra island":      "Quadra Island",
    "cortes island":      "Cortes Island",
    "gibsons":            "Gibsons",
    "sechelt":            "Sechelt",
    "roberts creek":      "Roberts Creek",
    "powell river":       "Powell River",
}

NON_MUSIC_RE = re.compile(
    r'\b(meat draw|easter|egg hunt|bingo|trivia|yoga|meditation|workshop|'
    r'art class|art show|gallery|exhibit|craft fair|market|sale|'
    r'fundraiser dinner|gala dinner|luncheon|brunch|paint night|pottery|'
    r'knitting|sewing|weaving|quilting|book launch|book club|library|'
    r'film night|movie night|cinema|fitness|seated fitness|'
    r'story time|storytime|playtime|scavenger hunt|'
    r'comedy show|stand.?up|improv|open mic)\b',
    re.I,
)


def normalise_city(raw: str) -> str:
    # Strip trailing ", BC" / " BC" suffixes
    cleaned = re.sub(r',?\s+bc$', '', (raw or "").strip(), flags=re.I)
    key = cleaned.lower()
    return CITY_FIXES.get(key, cleaned.title())


# ── Fetch ───────────────────────────────────────────────────────────────────────

def fetch_hub(token: str, lat: float, lon: float, radius_km: int, today: str) -> list[dict]:
    """Fetch music events within radius_km of (lat, lon) from today onward."""
    all_events: list[dict] = []
    page = 1

    while True:
        params = {
            "location.latitude":        lat,
            "location.longitude":       lon,
            "location.within":          f"{radius_km}km",
            "categories":               ",".join(MUSIC_CATEGORIES),
            "start_date.range_start":   f"{today}T00:00:00Z",
            "sort_by":                  "date",
            "expand":                   "venue,ticket_availability",
            "page_size":                PAGE_SIZE,
            "page":                     page,
        }
        url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept":        "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("  Rate limited — sleeping 10s …", file=sys.stderr)
                time.sleep(10)
                continue
            body = e.read().decode(errors="replace")
            print(f"  HTTP {e.code}: {e.reason} — {body[:120]}", file=sys.stderr)
            break
        except urllib.error.URLError as e:
            print(f"  Network error: {e.reason}", file=sys.stderr)
            break

        batch = data.get("events", [])
        all_events.extend(batch)

        pagination = data.get("pagination", {})
        if not pagination.get("has_more_items", False):
            break
        page += 1
        time.sleep(0.3)

    return all_events


# ── Convert ─────────────────────────────────────────────────────────────────────

def to_live_shows_event(raw: dict) -> dict | None:
    title = html.unescape((raw.get("name") or {}).get("text", "")).strip()

    if not title:
        return None

    # Filter non-music events by title
    if NON_MUSIC_RE.search(title):
        return None

    venue_obj  = raw.get("venue") or {}
    address    = venue_obj.get("address") or {}
    city_raw   = address.get("city", "")
    country    = address.get("country", "")

    # Only Canadian events
    if country and country.upper() not in ("CA", "CANADA"):
        return None

    start_obj  = raw.get("start") or {}
    start_local = start_obj.get("local", "")    # "2026-04-15T19:00:00"
    date_part  = start_local[:10] if start_local else None
    time_part  = start_local[11:16] if len(start_local) >= 16 else None   # "19:00"

    # Price from ticket_availability
    price = None
    ta = raw.get("ticket_availability") or {}
    min_price = (ta.get("minimum_ticket_price") or {}).get("major_value")
    max_price = (ta.get("maximum_ticket_price") or {}).get("major_value")
    if min_price:
        cur = (ta.get("minimum_ticket_price") or {}).get("currency", "CAD")
        if max_price and max_price != min_price:
            price = f"${min_price}–${max_price} {cur}"
        else:
            price = f"${min_price} {cur}"

    # Image
    logo = raw.get("logo") or {}
    image_url = (logo.get("original") or {}).get("url") or logo.get("url")

    return {
        "id":         f"{SOURCE_ID}:{raw['id']}",
        "title":      title,
        "venue":      (venue_obj.get("name") or "").strip() or None,
        "city":       normalise_city(city_raw),
        "date":       date_part,
        "start_time": time_part,
        "ticket_url": raw.get("url") or None,
        "price":      price,
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
    parser = argparse.ArgumentParser(description="Eventbrite scraper → events.json")
    parser.add_argument("--token",   default=os.environ.get("EB_TOKEN", ""),
                        help="Eventbrite private token (or set EB_TOKEN env var)")
    parser.add_argument("--write",   action="store_true", help="write results to events.json")
    parser.add_argument("--replace", action="store_true", help="replace all eventbrite events (implies --write)")
    args = parser.parse_args()

    if args.replace:
        args.write = True

    if not args.token:
        print("Error: Eventbrite token required. Set EB_TOKEN env var or pass --token.", file=sys.stderr)
        print("Free token: https://www.eventbrite.com/platform/api", file=sys.stderr)
        sys.exit(1)

    today     = datetime.now().strftime("%Y-%m-%d")
    all_raw:  list[dict] = []
    seen_ids: set[str]   = set()

    for label, lat, lon, radius in SEARCH_HUBS:
        print(f"Fetching hub: {label} ({lat},{lon} +{radius}km) …", file=sys.stderr)
        batch = fetch_hub(args.token, lat, lon, radius, today)
        new_here = 0
        for e in batch:
            if e["id"] not in seen_ids:
                seen_ids.add(e["id"])
                all_raw.append(e)
                new_here += 1
        print(f"  {len(batch)} fetched, {new_here} new unique.", file=sys.stderr)
        time.sleep(0.5)

    print(f"\n{len(all_raw)} unique raw events. Converting …", file=sys.stderr)
    incoming = [ev for raw in all_raw
                if (ev := to_live_shows_event(raw)) and ev["title"] and ev["date"]]
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
