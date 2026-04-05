#!/usr/bin/env python3
"""
Sid Williams Theatre scraper — fetches upcoming events from
sidwilliamstheatre.com and merges them into projects/live-shows/events.json.

The site uses a custom CMS with server-rendered HTML. No API or Playwright needed.

Music filter strategy:
  - "Commercial Presenters" series: keep all (professional touring acts), minus
    non-music titles caught by NON_MUSIC_RE.
  - All other series: only keep if title matches MUSIC_RE (music keyword whitelist).

Usage:
    python3 scrapers/sidwilliams_scraper.py             # dry-run
    python3 scrapers/sidwilliams_scraper.py --write     # merge
    python3 scrapers/sidwilliams_scraper.py --replace   # re-sync
"""

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

EVENTS_JSON = Path(__file__).parent.parent / "projects" / "live-shows" / "events.json"
SOURCE_ID   = "sidwilliams"
EVENTS_URL  = "https://www.sidwilliamstheatre.com/events/"
BASE_URL    = "https://www.sidwilliamstheatre.com"
VENUE_NAME  = "Sid Williams Theatre"
CITY        = "Comox Valley"

# Keep all events from these series (still subject to NON_MUSIC_RE)
KEEP_SERIES = {"commercial presenters", "blue circle series"}

# For all other series, require at least one music keyword
MUSIC_RE = re.compile(
    r'\b(tribute|concert|music|musical|jazz|blues|folk|rock|opera|classical|'
    r'symphony|ensemble|band|tour|country|pop|rap|hip.hop|r&b|'
    r'strings|fiddle|acoustic|songwriter|singer|choir|choral|'
    r'quartet|quintet|duo|trio|orchestra|cabaret|variety|revue)\b',
    re.I,
)

NON_MUSIC_RE = re.compile(
    r'\b(comedy|film festival|film fest|lecture|workshop|dance recital|'
    r'youth media|learning|reconciliation|imagefest|director.s cut|'
    r'panel discussion|speaker|cancelled)\b',
    re.I,
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "Wed Apr 8, 2026" or "Apr 15-19, 2026" or "Sep 10, 2026"
DATE_RE = re.compile(r'(\w{3})\s+(\d{1,2})(?:-\d+)?,\s+(\d{4})', re.I)


def parse_date(text: str) -> str | None:
    m = DATE_RE.search(text.strip())
    if not m:
        return None
    mon_str, day, year = m.groups()
    month = MONTHS.get(mon_str.lower())
    if not month:
        return None
    return f"{year}-{month:02d}-{int(day):02d}"


def fetch_events_html() -> str:
    req = urllib.request.Request(EVENTS_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        print(f"Network error: {e.reason}", file=sys.stderr)
        sys.exit(1)


def parse_events(page_html: str) -> list[dict]:
    """Extract events from the Sid Williams events page HTML."""
    events = []

    # Each event card: <div class='item ... series-N'>...(card content)...</div>
    item_re = re.compile(
        r"<div class='item[^']*'>(.*?)</div>\s*</div>\s*</div>",
        re.S,
    )
    title_re  = re.compile(r"<h3 class='uk-card-title'><a href='([^']+)'[^>]*>([^<]+)</a>", re.S)
    series_re = re.compile(r"<p class='series'><a[^>]+>([^<]+)</a>", re.S)
    dates_re  = re.compile(r"<p class='dates'>([^<]+)</p>", re.S)
    img_re    = re.compile(r"<img src='([^']+)'[^>]*alt='[^']*'", re.S)

    for m in item_re.finditer(page_html):
        card = m.group(1)

        title_m = title_re.search(card)
        if not title_m:
            continue
        slug_path = title_m.group(1)   # e.g. /events/three-for-the-tillerman.../
        title     = html.unescape(title_m.group(2).strip())

        series_m  = series_re.search(card)
        series    = series_m.group(1).strip().lower() if series_m else ""

        dates_m   = dates_re.search(card)
        date_txt  = dates_m.group(1).strip() if dates_m else ""
        date_part = parse_date(date_txt)

        img_m     = img_re.search(card)
        image_url = (BASE_URL + img_m.group(1)) if img_m else None

        ticket_url = BASE_URL + slug_path

        # Music filter
        if series in KEEP_SERIES:
            if NON_MUSIC_RE.search(title):
                continue
        else:
            if not MUSIC_RE.search(title):
                continue
            if NON_MUSIC_RE.search(title):
                continue

        # Stable ID from slug
        slug = slug_path.strip("/").split("/")[-1]
        event_id = f"{SOURCE_ID}:{slug}"

        events.append({
            "id":         event_id,
            "title":      title,
            "venue":      VENUE_NAME,
            "city":       CITY,
            "date":       date_part,
            "start_time": None,   # not available on listing page
            "ticket_url": ticket_url,
            "price":      None,
            "image_url":  image_url,
            "genre":      None,
        })

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
    parser = argparse.ArgumentParser(description="Sid Williams Theatre scraper → events.json")
    parser.add_argument("--write",   action="store_true", help="write results to events.json")
    parser.add_argument("--replace", action="store_true", help="replace all sidwilliams events (implies --write)")
    args = parser.parse_args()
    if args.replace:
        args.write = True

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"Fetching {EVENTS_URL} …", file=sys.stderr)
    page_html = fetch_events_html()

    all_events = parse_events(page_html)
    incoming   = [e for e in all_events if e.get("date") and e["date"] >= today]
    print(f"  {len(all_events)} music events parsed, {len(incoming)} upcoming.", file=sys.stderr)

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
