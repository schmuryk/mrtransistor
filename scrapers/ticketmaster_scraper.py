#!/usr/bin/env python3
"""
Ticketmaster Discovery API scraper — fetches music events on Vancouver Island,
Gulf Islands, and Sunshine Coast, then merges into projects/live-shows/events.json.

LiveNation events appear here automatically (same parent company, same API).

API key: free registration at https://developer.ticketmaster.com/
Export TM_KEY=<your_consumer_key> before running.

Usage:
    TM_KEY=xxx python3 scrapers/ticketmaster_scraper.py             # dry-run
    TM_KEY=xxx python3 scrapers/ticketmaster_scraper.py --write     # merge
    TM_KEY=xxx python3 scrapers/ticketmaster_scraper.py --replace   # re-sync
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EVENTS_JSON = Path(__file__).parent.parent / "projects" / "live-shows" / "events.json"
SOURCE_ID   = "ticketmaster"
API_BASE    = "https://app.ticketmaster.com/discovery/v2/events.json"
PAGE_SIZE   = 200   # TM max

# Hub points: (label, lat, lon, radius_km).
# Radius chosen to capture the hub city + nearby small towns without too much overlap.
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

CITY_FIXES = {
    "victoria":          "Victoria",
    "saanich":           "Victoria",
    "langford":          "Victoria",
    "colwood":           "Victoria",
    "oak bay":           "Victoria",
    "esquimalt":         "Victoria",
    "view royal":        "Victoria",
    "central saanich":   "Victoria",
    "north saanich":     "Victoria",
    "nanaimo":           "Nanaimo",
    "ladysmith":         "Ladysmith",
    "chemainus":         "Chemainus",
    "crofton":           "Crofton",
    "duncan":            "Duncan",
    "cowichan bay":      "Cowichan Bay",
    "lake cowichan":     "Lake Cowichan",
    "port alberni":      "Port Alberni",
    "tofino":            "Tofino",
    "ucluelet":          "Ucluelet",
    "parksville":        "Parksville",
    "qualicum beach":    "Qualicum Beach",
    "qualicum":          "Qualicum Beach",
    "courtenay":         "Comox Valley",
    "comox":             "Comox Valley",
    "cumberland":        "Comox Valley",
    "campbell river":    "Campbell River",
    "port mcneill":      "Port McNeill",
    "port hardy":        "Port Hardy",
    "salt spring island":"Salt Spring Island",
    "salt spring":       "Salt Spring Island",
    "ganges":            "Salt Spring Island",
    "pender island":     "Pender Island",
    "galiano island":    "Galiano Island",
    "mayne island":      "Mayne Island",
    "hornby island":     "Hornby Island",
    "denman island":     "Denman Island",
    "quadra island":     "Quadra Island",
    "cortes island":     "Cortes Island",
    "gibsons":           "Gibsons",
    "sechelt":           "Sechelt",
    "roberts creek":     "Roberts Creek",
    "powell river":      "Powell River",
}


def normalise_city(raw: str) -> str:
    key = (raw or "").strip().lower()
    return CITY_FIXES.get(key, (raw or "").strip().title())


# ── Fetch ───────────────────────────────────────────────────────────────────────

def fetch_hub(api_key: str, lat: float, lon: float, radius_km: int, today: str) -> list[dict]:
    """Fetch all music events within radius_km of (lat, lon) from today onward."""
    all_events: list[dict] = []
    page = 0

    while True:
        params = {
            "apikey":             api_key,
            "classificationName": "Music",
            "latlong":            f"{lat},{lon}",
            "radius":             radius_km,
            "unit":               "km",
            "startDateTime":      f"{today}T00:00:00Z",
            "size":               PAGE_SIZE,
            "page":               page,
            "sort":               "date,asc",
            "countryCode":        "CA",
        }
        url = f"{API_BASE}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})

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

        embedded   = data.get("_embedded", {})
        batch      = embedded.get("events", [])
        all_events.extend(batch)

        page_info   = data.get("page", {})
        total_pages = page_info.get("totalPages", 1)
        if page + 1 >= total_pages or not batch:
            break
        page += 1
        time.sleep(0.25)   # ~4 req/s (TM free tier: 5 req/s)

    return all_events


# ── Convert ─────────────────────────────────────────────────────────────────────

def best_image(images: list[dict]) -> str | None:
    """Return URL of widest 16:9 image, fallback to widest any-ratio."""
    candidates = [i for i in images if i.get("ratio") == "16_9"]
    pool = candidates or images
    if not pool:
        return None
    return max(pool, key=lambda i: i.get("width", 0)).get("url")


def to_live_shows_event(raw: dict) -> dict | None:
    # Confirm music segment
    classifications = raw.get("classifications", [])
    if not any(c.get("segment", {}).get("name", "").lower() == "music"
               for c in classifications):
        return None

    venues    = (raw.get("_embedded") or {}).get("venues", [{}])
    venue_obj = venues[0] if venues else {}
    city_raw  = (venue_obj.get("city") or {}).get("name", "")
    state_raw = (venue_obj.get("state") or {}).get("stateCode", "")

    # Only keep BC events
    if state_raw.upper() != "BC":
        return None

    start      = (raw.get("dates") or {}).get("start") or {}
    date_part  = start.get("localDate")
    local_time = start.get("localTime") or ""
    time_part  = local_time[:5] if len(local_time) >= 5 else None   # "19:30"

    # Genre from first classification
    genre = None
    if classifications:
        c  = classifications[0]
        g  = (c.get("genre") or {}).get("name", "")
        sg = (c.get("subGenre") or {}).get("name", "")
        parts = [p for p in [g, sg] if p and p.lower() not in ("undefined", "other")]
        genre = ", ".join(parts) or None

    # Price
    price = None
    ranges = raw.get("priceRanges") or []
    if ranges:
        lo  = ranges[0].get("min")
        hi  = ranges[0].get("max")
        cur = ranges[0].get("currency", "CAD")
        if lo is not None and hi is not None and lo != hi:
            price = f"${lo:.0f}–${hi:.0f} {cur}"
        elif lo is not None:
            price = f"${lo:.0f} {cur}"

    return {
        "id":         f"{SOURCE_ID}:{raw['id']}",
        "title":      (raw.get("name") or "").strip(),
        "venue":      (venue_obj.get("name") or "").strip() or None,
        "city":       normalise_city(city_raw),
        "date":       date_part,
        "start_time": time_part,
        "ticket_url": raw.get("url") or None,
        "price":      price,
        "image_url":  best_image(raw.get("images") or []),
        "genre":      genre,
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
    parser = argparse.ArgumentParser(description="Ticketmaster (+ LiveNation) scraper → events.json")
    parser.add_argument("--key",     default=os.environ.get("TM_KEY", ""),
                        help="Ticketmaster API key (or set TM_KEY env var)")
    parser.add_argument("--write",   action="store_true", help="write results to events.json")
    parser.add_argument("--replace", action="store_true", help="replace all ticketmaster events (implies --write)")
    args = parser.parse_args()

    if args.replace:
        args.write = True

    if not args.key:
        print("Error: Ticketmaster API key required. Set TM_KEY env var or pass --key.", file=sys.stderr)
        print("Free key: https://developer.ticketmaster.com/", file=sys.stderr)
        sys.exit(1)

    today     = datetime.now().strftime("%Y-%m-%d")
    all_raw:  list[dict] = []
    seen_ids: set[str]   = set()

    for label, lat, lon, radius in SEARCH_HUBS:
        print(f"Fetching hub: {label} ({lat},{lon} +{radius}km) …", file=sys.stderr)
        batch = fetch_hub(args.key, lat, lon, radius, today)
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
