#!/usr/bin/env python3
"""
vieme.ca scraper — fetches approved electronic music events and merges them
into projects/live-shows/events.json so the live-shows app can display them.

The site is a base44 React SPA with a public REST API (no auth needed).

Usage:
    python3 scrapers/vieme_scraper.py            # dry-run: print events to add
    python3 scrapers/vieme_scraper.py --write     # merge into events.json
    python3 scrapers/vieme_scraper.py --replace   # replace all vieme events (re-sync)
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
APP_ID     = "69557f7650f2cd95fbde4488"
API_URL    = f"https://vieme.ca/api/apps/{APP_ID}/entities/Event"
PAGE_SIZE  = 200
SOURCE_ID  = "vieme"
EVENTS_JSON = Path(__file__).parent.parent / "projects" / "live-shows" / "events.json"

# Vieme cities → normalised city strings the app understands
CITY_MAP = {
    "victoria": "Victoria",
    "nanaimo": "Nanaimo",
    "cumberland": "Comox Valley",
    "courtenay": "Comox Valley",
    "duncan": "Duncan",
    "campbell river": "Campbell River",
    "port mcneill": "Port McNeill",
    "port mcneil": "Port McNeill",
    "bamfield": "Bamfield",
    "parksville": "Parksville",
    "qualicum": "Qualicum Beach",
    "comox": "Comox Valley",
    "ladysmith": "Ladysmith",
    "lake cowichan": "Lake Cowichan",
    "tofino": "Tofino",
    "ucluelet": "Ucluelet",
}


# ── Fetch ───────────────────────────────────────────────────────────────────────
def fetch_events() -> list[dict]:
    all_events: list[dict] = []
    skip = 0

    while True:
        params = {
            "limit": PAGE_SIZE,
            "skip": skip,
            "sort": "event_date",
            "q": json.dumps({"status": "approved"}),
        }
        url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                batch = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"HTTP error {e.code}: {e.reason}", file=sys.stderr)
            sys.exit(1)
        except urllib.error.URLError as e:
            print(f"Network error: {e.reason}", file=sys.stderr)
            sys.exit(1)

        if not isinstance(batch, list) or not batch:
            break

        all_events.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        skip += PAGE_SIZE

    return all_events


# ── Convert ─────────────────────────────────────────────────────────────────────
def normalise_city(raw: str) -> str:
    key = (raw or "").strip().lower()
    return CITY_MAP.get(key, (raw or "").strip().title())


def parse_date(iso: str) -> str:
    """Convert '2026-04-11T05:00:00.000Z' → '2026-04-11'."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        # vieme stores dates as midnight UTC; convert to Pacific to get the local date.
        # Approximate: subtract 7h (PDT) or 8h (PST). Use date portion as-is since
        # vieme already stores the event day in UTC midnight format for BC events.
        return dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return (iso or "")[:10]


def to_live_shows_event(e: dict) -> dict:
    genres = e.get("genres") or []
    return {
        "id":         f"{SOURCE_ID}:{e['id']}",
        "title":      (e.get("event_name") or "").strip(),
        "venue":      (e.get("event_location") or "").strip(),
        "city":       normalise_city(e.get("city") or ""),
        "date":       parse_date(e.get("event_date") or ""),
        "start_time": None,
        "ticket_url": e.get("ticket_link") or None,
        "price":      None,
        "image_url":  e.get("event_image") or None,
        "genre":      ", ".join(genres) if genres else None,
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
    """
    Returns (merged_list, added_count, replaced_count).
    - replace=False: skip events whose id already exists.
    - replace=True: overwrite existing vieme events, add new ones.
    """
    if replace:
        non_vieme = [e for e in existing if not e["id"].startswith(f"{SOURCE_ID}:")]
        incoming_ids = {e["id"] for e in incoming}
        old_vieme = {e["id"] for e in existing if e["id"].startswith(f"{SOURCE_ID}:")}
        replaced = len(old_vieme & incoming_ids)
        added = len(incoming_ids - old_vieme)
        merged = non_vieme + incoming
    else:
        existing_ids = {e["id"] for e in existing}
        new_events = [e for e in incoming if e["id"] not in existing_ids]
        added = len(new_events)
        replaced = 0
        merged = existing + new_events

    # Sort by date
    merged.sort(key=lambda e: e.get("date") or "")
    return merged, added, replaced


# ── Main ────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape vieme.ca → events.json")
    parser.add_argument("--write",   action="store_true", help="write results to events.json")
    parser.add_argument("--replace", action="store_true", help="replace all vieme events (implies --write)")
    args = parser.parse_args()

    if args.replace:
        args.write = True

    print(f"Fetching approved events from vieme.ca …", file=sys.stderr)
    raw = fetch_events()
    incoming = [to_live_shows_event(e) for e in raw]
    print(f"  Got {len(incoming)} events from API.", file=sys.stderr)

    data = load_events_json()
    existing = data.get("events", [])
    print(f"  {len(existing)} events currently in events.json.", file=sys.stderr)

    merged, added, replaced = merge(existing, incoming, replace=args.replace)

    if args.write:
        data["events"] = merged
        data["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        save_events_json(data)
        print(f"  Written: {added} added, {replaced} replaced. Total: {len(merged)}.", file=sys.stderr)
    else:
        # Dry-run: show what would be added
        existing_ids = {e["id"] for e in existing}
        new_events = [e for e in incoming if e["id"] not in existing_ids]
        if not new_events:
            print("  No new events to add (all already present).", file=sys.stderr)
        else:
            print(f"  {len(new_events)} new event(s) would be added (dry-run, use --write):\n", file=sys.stderr)
            for e in new_events:
                print(f"  {e['date']}  {e['city']:<16}  {e['title'][:60]}")


if __name__ == "__main__":
    main()
