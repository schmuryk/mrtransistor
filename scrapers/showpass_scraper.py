#!/usr/bin/env python3
"""
Showpass scraper — fetches music events from Showpass (Canadian ticketing platform
popular with BC venues) and merges into projects/live-shows/events.json.

No API key required — uses the public JSON API.

Showpass is used by many VI venues not covered by Ticketmaster or Eventbrite,
e.g. venues in Nanaimo, Victoria, and smaller island communities.

Usage:
    python3 scrapers/showpass_scraper.py             # dry-run
    python3 scrapers/showpass_scraper.py --write     # merge
    python3 scrapers/showpass_scraper.py --replace   # re-sync
"""

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EVENTS_JSON = Path(__file__).parent.parent / "projects" / "live-shows" / "events.json"
SOURCE_ID   = "showpass"
API_BASE    = "https://www.showpass.com/api/public/events/"
PAGE_SIZE   = 100

# Search terms to find events in the region.
# Showpass doesn't support lat/lon search, so we query by city name.
SEARCH_CITIES = [
    "Victoria",
    "Nanaimo",
    "Courtenay",
    "Comox",
    "Campbell River",
    "Duncan",
    "Port Alberni",
    "Parksville",
    "Qualicum",
    "Tofino",
    "Ucluelet",
    "Ladysmith",
    "Powell River",
    "Sechelt",
    "Gibsons",
    "Salt Spring Island",
]

CITY_FIXES = {
    "victoria":           "Victoria",
    "saanich":            "Victoria",
    "langford":           "Victoria",
    "colwood":            "Victoria",
    "oak bay":            "Victoria",
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

# Sports events often contain "Team A vs. Team B" — skip them.
SPORTS_VS_RE = re.compile(r'\bvs\.?\s+\w', re.I)

# Cities we actually want. Events whose normalised city isn't in this set are skipped
# (catches out-of-region results where "Victoria" matched in the event title).
TARGET_CITIES = set(CITY_FIXES.values())


def normalise_city(raw: str) -> str:
    cleaned = re.sub(r',?\s+bc$', '', (raw or "").strip(), flags=re.I)
    key = cleaned.lower()
    return CITY_FIXES.get(key, cleaned.title())


# ── Fetch ───────────────────────────────────────────────────────────────────────

def fetch_city(city: str, today: str) -> list[dict]:
    """Fetch events in a city from today onward."""
    all_events: list[dict] = []
    page = 1

    while True:
        params = {
            "format":          "json",
            "q":               city,
            "timestamp_start": today,
            "page_size":       PAGE_SIZE,
            "page":            page,
            "ordering":        "starts_on",
        }
        url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept":     "application/json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("  Rate limited — sleeping 10s …", file=sys.stderr)
                time.sleep(10)
                continue
            print(f"  HTTP {e.code} for '{city}'", file=sys.stderr)
            break
        except urllib.error.URLError as e:
            print(f"  Network error for '{city}': {e.reason}", file=sys.stderr)
            break
        except json.JSONDecodeError:
            print(f"  Non-JSON response for '{city}' — skipping", file=sys.stderr)
            break

        # Showpass returns {"count": N, "num_pages": N, "page_number": N, "results": [...]}
        results = data.get("results", [])
        if not results:
            break
        all_events.extend(results)

        if page >= data.get("num_pages", 1):
            break
        page += 1
        time.sleep(0.3)

    return all_events


# ── Convert ─────────────────────────────────────────────────────────────────────

def to_live_shows_event(raw: dict) -> dict | None:
    title = html.unescape((raw.get("name") or "")).strip()
    if not title:
        return None

    if NON_MUSIC_RE.search(title):
        return None

    if SPORTS_VS_RE.search(title):
        return None

    # Location object has venue name + city + country
    loc        = raw.get("location") or {}
    venue_name = loc.get("name") or (raw.get("venue") or {}).get("name", "")
    city_raw   = loc.get("city") or (raw.get("venue") or {}).get("city", "") or ""
    country    = (loc.get("country") or "").upper()

    # Only Canadian events
    if country and country not in ("CA", "CANADA", "CAN"):
        return None

    city = normalise_city(city_raw)

    # Skip events whose city isn't in our target region
    # (catches results where the search term matched the event title, not the location)
    if city not in TARGET_CITIES:
        return None

    # local_starts_on: "2026-04-10T22:00:00-07:00" — already in local timezone
    starts_on = raw.get("local_starts_on") or raw.get("starts_on") or ""
    date_part = starts_on[:10] if starts_on else None
    time_part = starts_on[11:16] if len(starts_on) >= 16 else None   # "22:00"

    # Price: derive min/max from ticket_types[].price (skip free/None)
    price = None
    prices = [
        float(tt["price"])
        for tt in (raw.get("ticket_types") or [])
        if tt.get("price") is not None and float(tt["price"]) > 0
    ]
    if prices:
        lo, hi = min(prices), max(prices)
        if lo != hi:
            price = f"${lo:.0f}–${hi:.0f} CAD"
        else:
            price = f"${lo:.0f} CAD"

    # Image — full-size preferred
    image_url = raw.get("image") or raw.get("thumbnail") or None

    # Ticket URL
    ticket_url = raw.get("frontend_details_url") or None

    return {
        "id":         f"{SOURCE_ID}:{raw['id']}",
        "title":      title,
        "venue":      (venue_name or "").strip() or None,
        "city":       city,
        "date":       date_part,
        "start_time": time_part,
        "ticket_url": ticket_url,
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
    parser = argparse.ArgumentParser(description="Showpass scraper → events.json")
    parser.add_argument("--write",   action="store_true", help="write results to events.json")
    parser.add_argument("--replace", action="store_true", help="replace all showpass events (implies --write)")
    args = parser.parse_args()

    if args.replace:
        args.write = True

    today     = datetime.now().strftime("%Y-%m-%d")
    all_raw:  list[dict] = []
    seen_ids: set[str]   = set()

    for city in SEARCH_CITIES:
        print(f"Fetching Showpass: {city} …", file=sys.stderr)
        batch = fetch_city(city, today)
        new_here = 0
        for e in batch:
            eid = str(e.get("id", ""))
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                all_raw.append(e)
                new_here += 1
        print(f"  {len(batch)} fetched, {new_here} new unique.", file=sys.stderr)
        time.sleep(0.4)

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
