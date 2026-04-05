#!/usr/bin/env python3
"""
Clayoquot Sound Theatre scraper — fetches upcoming events from
tofinotheatre.com and merges them into projects/live-shows/events.json.

The site is a Wix SPA with client-side rendering, so we use Playwright
(headless Chromium) to load the page before extracting event data.

Playwright + Chromium must be installed:
    pip3 install playwright --break-system-packages
    python3 -m playwright install chromium

Usage:
    python3 scrapers/tofinotheatre_scraper.py             # dry-run
    python3 scrapers/tofinotheatre_scraper.py --write     # merge
    python3 scrapers/tofinotheatre_scraper.py --replace   # re-sync
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

EVENTS_JSON = Path(__file__).parent.parent / "projects" / "live-shows" / "events.json"
SOURCE_ID   = "tofinotheatre"
EVENTS_URL  = "https://www.tofinotheatre.com/events-1"
VENUE_NAME  = "Clayoquot Sound Theatre"
CITY        = "Tofino"
WIX_CDN     = "https://static.wixstatic.com/media/"

# "Apr 04, 2026, 8:00 PM" or "Apr 04, 2026, 8:00 PM – 10:20 PM"
DATE_RE = re.compile(
    r'(\w{3})\s+(\d{1,2}),\s+(\d{4}),\s+(\d{1,2}):(\d{2})\s+(AM|PM)',
    re.I,
)
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_event_date(text: str) -> tuple[str | None, str | None]:
    """Parse 'Apr 04, 2026, 8:00 PM' → ('2026-04-04', '20:00')."""
    m = DATE_RE.search(text)
    if not m:
        return None, None
    mon_str, day, year, hour, minute, ampm = m.groups()
    month = MONTHS.get(mon_str.lower())
    if not month:
        return None, None
    h = int(hour)
    if ampm.upper() == "PM" and h != 12:
        h += 12
    elif ampm.upper() == "AM" and h == 12:
        h = 0
    date_part = f"{year}-{month:02d}-{int(day):02d}"
    time_part = f"{h:02d}:{minute}"
    return date_part, time_part


def scrape_events() -> list[dict]:
    """Launch headless Chromium, load the events page, extract event data."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright not installed. Run:\n"
            "  pip3 install playwright --break-system-packages\n"
            "  python3 -m playwright install chromium",
            file=sys.stderr,
        )
        sys.exit(1)

    events = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        print(f"Loading {EVENTS_URL} …", file=sys.stderr)
        page.goto(EVENTS_URL, wait_until="networkidle", timeout=40000)

        items = page.query_selector_all('[data-hook="side-by-side-item"]')
        print(f"  Found {len(items)} event items.", file=sys.stderr)

        for item in items:
            title_el = item.query_selector('[data-hook="title"]')
            title = (title_el.inner_text() if title_el else "").strip()
            if not title:
                continue

            date_el = item.query_selector('[data-hook="date"]')
            date_txt = (date_el.inner_text() if date_el else "").strip()
            date_part, time_part = parse_event_date(date_txt)

            btn_el = item.query_selector('[data-hook="ev-rsvp-button"]')
            ticket_url = (btn_el.get_attribute("href") if btn_el else None) or None

            # Image from Wix wow-image data-image-info JSON
            image_url = None
            wow_el = item.query_selector("wow-image")
            if wow_el:
                info_str = wow_el.get_attribute("data-image-info") or ""
                try:
                    info = json.loads(info_str)
                    uri = info.get("imageData", {}).get("uri", "")
                    if uri:
                        image_url = WIX_CDN + uri
                except (json.JSONDecodeError, AttributeError):
                    pass

            # Stable ID: slugify title + date
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
            event_id = f"{SOURCE_ID}:{slug}-{date_part}" if date_part else f"{SOURCE_ID}:{slug}"

            events.append({
                "id":         event_id,
                "title":      title,
                "venue":      VENUE_NAME,
                "city":       CITY,
                "date":       date_part,
                "start_time": time_part,
                "ticket_url": ticket_url,
                "price":      None,
                "image_url":  image_url,
                "genre":      None,
            })

        browser.close()

    return events


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
        kept    = [e for e in existing if not e["id"].startswith(f"{SOURCE_ID}:")]
        old_ids = {e["id"] for e in existing if e["id"].startswith(f"{SOURCE_ID}:")}
        new_ids = {e["id"] for e in incoming}
        merged  = kept + incoming
        added, replaced = len(new_ids - old_ids), len(old_ids & new_ids)
    else:
        existing_ids = {e["id"] for e in existing}
        new_events   = [e for e in incoming if e["id"] not in existing_ids]
        merged = existing + new_events
        added, replaced = len(new_events), 0

    merged.sort(key=lambda e: e.get("date") or "")
    return merged, added, replaced


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Clayoquot Sound Theatre scraper → events.json")
    parser.add_argument("--write",   action="store_true", help="write results to events.json")
    parser.add_argument("--replace", action="store_true", help="replace all tofinotheatre events (implies --write)")
    args = parser.parse_args()
    if args.replace:
        args.write = True

    today    = datetime.now().strftime("%Y-%m-%d")
    incoming = [e for e in scrape_events() if e.get("date") and e["date"] >= today]
    print(f"{len(incoming)} upcoming events found.", file=sys.stderr)

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
