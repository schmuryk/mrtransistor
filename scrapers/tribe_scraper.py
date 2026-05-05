#!/usr/bin/env python3
"""
Generic scraper for sites running The Events Calendar (Tribe) WordPress plugin.
Merges results into projects/live-shows/events.json.

Sites covered:
  whatsonvancouverisland  — VI-wide: Cowichan, Pacific Rim, Parksville, Comox, Campbell River
  coastculture            — Sunshine Coast: Gibsons, Sechelt, Powell River
  gulfislandevents        — Gulf Islands: Salt Spring, Pender, Galiano, Mayne, Saturna
  performingartscomoxvalley — Comox Valley performing arts
  campbellrivernow        — Campbell River live music
  qathet365               — Powell River
  cowichanpac             — Cowichan Performing Arts Centre (Duncan)

Usage:
    python3 scrapers/tribe_scraper.py                  # dry-run all sites
    python3 scrapers/tribe_scraper.py --write           # merge into events.json
    python3 scrapers/tribe_scraper.py --site wovi       # single site
    python3 scrapers/tribe_scraper.py --replace --write # re-sync
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
PAGE_SIZE   = 50   # Tribe default max

# ── Site configs ────────────────────────────────────────────────────────────────
# category_ids: only fetch events in these category IDs (None = all events)
# exclude_categories: skip events that have any of these category names (lowercase)
SITES = {
    "wovi": {
        "name": "whatsonvancouverisland",
        "base": "https://whatsonvancouverisland.com",
        "category_ids": [324],        # Live Music
        "exclude_cats": {"sports", "education", "tours", "markets", "expo"},
        # Victoria & Nanaimo already well-covered by dedicated scrapers
        "exclude_cities": {"victoria", "nanaimo", "saanich", "oak bay", "esquimalt", "langford", "colwood", "view royal", "central saanich", "north saanich"},
        "start_date": True,
    },
    "coastculture": {
        "name": "coastculture",
        "base": "https://coastculture.com",
        "category_ids": [94],          # Performance/Music
        "exclude_cats": {"sports/exercise/yoga"},
        "start_date": True,
    },
    "gulfislands": {
        "name": "gulfislandevents",
        "base": "https://gulfislandevents.com",
        "category_ids": [15],          # Music
        "exclude_cats": set(),
        "start_date": True,
    },
    "pacv": {
        "name": "performingartscomoxvalley",
        "base": "https://performingartscomoxvalley.ca",
        "category_ids": None,          # all — only 28 events total
        "exclude_cats": set(),
        "start_date": True,
    },
    "crnow": {
        "name": "campbellrivernow",
        "base": "https://campbellrivernow.com",
        "category_ids": None,          # no music category — fetch all, filter by title later
        "exclude_cats": {"sports", "education", "fitness"},
        "start_date": True,
    },
    "qathet": {
        "name": "qathet365",
        "base": "https://qathet365.ca",
        "category_ids": [1374],        # MUSIC, FILM & COMEDY
        "exclude_cats": set(),
        "start_date": True,
    },
    "cowichanpac": {
        "name": "cowichanpac",
        "base": "https://cowichanpac.ca",
        "category_ids": [16, 17],      # Music, Comedy
        "exclude_cats": set(),
        "default_city":  "Duncan",     # many events have no venue/city set
        "default_venue": "Cowichan Performing Arts Centre",
        "start_date": True,
    },
    "artspring": {
        "name": "artspring",
        "base": "https://artspring.ca",
        # 28=ArtSpring Presents, 115=Musical Performance, 45=Chamber Music Festival
        "category_ids": [28, 115, 45],
        "exclude_cats": {"youth", "gallery exhibits & events", "lobby exhibits",
                         "film", "rental", "treasure fair", "workshop"},
        "default_city":  "Salt Spring Island",
        "default_venue": "ArtSpring",
        "start_date": True,
    },
}

# Normalise common VI/Sunshine Coast city names to clean title case
CITY_FIXES = {
    "victoria":             "Victoria",
    "nanaimo":              "Nanaimo",
    "duncan":               "Duncan",
    "cowichan bay":         "Cowichan Bay",
    "lake cowichan":        "Lake Cowichan",
    "ladysmith":            "Ladysmith",
    "chemainus":            "Chemainus",
    "crofton":              "Crofton",
    "port alberni":         "Port Alberni",
    "tofino":               "Tofino",
    "ucluelet":             "Ucluelet",
    "parksville":           "Parksville",
    "qualicum beach":       "Qualicum Beach",
    "qualicum":             "Qualicum Beach",
    "courtenay":            "Comox Valley",
    "comox":                "Comox Valley",
    "cumberland":           "Comox Valley",
    "black creek":          "Comox Valley",
    "merville":             "Comox Valley",
    "campbell river":       "Campbell River",
    "port mcneill":         "Port McNeill",
    "port mcneil":          "Port McNeill",
    "port hardy":           "Port Hardy",
    "telegraph cove":       "Telegraph Cove",
    "salt spring island":   "Salt Spring Island",
    "salt spring":          "Salt Spring Island",
    "saltspring island":    "Salt Spring Island",
    "ganges":               "Salt Spring Island",
    "pender island":        "Pender Island",
    "north pender island":  "Pender Island",
    "south pender island":  "Pender Island",
    "galiano island":       "Galiano Island",
    "galiano":              "Galiano Island",
    "mayne island":         "Mayne Island",
    "saturna island":       "Saturna Island",
    "hornby island":        "Hornby Island",
    "denman island":        "Denman Island",
    "quadra island":        "Quadra Island",
    "cortes island":        "Cortes Island",
    "gibsons":              "Gibsons",
    "sechelt":              "Sechelt",
    "roberts creek":        "Roberts Creek",
    "halfmoon bay":         "Halfmoon Bay",
    "pender harbour":       "Pender Harbour",
    "madeira park":         "Madeira Park",
    "powell river":         "Powell River",
    "lund":                 "Lund",
}


# ── Fetch ───────────────────────────────────────────────────────────────────────
def fetch_site(cfg: dict) -> list[dict]:
    base    = cfg["base"]
    cat_ids = cfg.get("category_ids")
    today   = datetime.now().strftime("%Y-%m-%d") if cfg.get("start_date") else None

    all_events: list[dict] = []
    page = 1

    while True:
        params: dict = {"per_page": PAGE_SIZE, "page": page}
        if cat_ids:
            params["categories"] = ",".join(str(i) for i in cat_ids)
        if today:
            params["start_date"] = today

        url = f"{base}/wp-json/tribe/events/v1/events?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            print(f"  HTTP {e.code} from {base}", file=sys.stderr)
            break
        except urllib.error.URLError as e:
            print(f"  Network error from {base}: {e.reason}", file=sys.stderr)
            break
        except json.JSONDecodeError as e:
            print(f"  Invalid JSON from {base}: {e}", file=sys.stderr)
            break

        batch = data.get("events", [])
        if not batch:
            break

        all_events.extend(batch)

        total_pages = data.get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.3)   # be polite

    return all_events


# ── Convert ─────────────────────────────────────────────────────────────────────
def normalise_city(raw: str) -> str:
    # Strip trailing ", BC" / " BC" / " Bc" suffixes before lookup
    cleaned = re.sub(r',?\s+bc$', '', (raw or "").strip(), flags=re.I)
    key = cleaned.lower()
    return CITY_FIXES.get(key, cleaned.title())


NON_MUSIC_RE = re.compile(
    r'\b(meat draw|easter|egg hunt|bingo|karaoke|trivia|yoga|meditation|workshop|'
    r'art class|art show|gallery|exhibit|craft fair|market|sale|'
    r'fundraiser dinner|gala dinner|luncheon|brunch|paint night|pottery|'
    r'knitting|sewing|weaving|quilting|book launch|book club|library|'
    r'film night|movie night|cinema|fitness|seated fitness|'
    r'story time|storytime|playtime|tea &|tea and|'
    r'scavenger hunt|easter celebration|easter sunday|'
    # sports & recreation
    r'bowling|pickleball|table tennis|ping.?pong|badminton|tai chi|skating|'
    r'autocross|drag racing|monster trucks?|wrestling|ufc|fight night|'
    r'car show|show n shine|volleyball|'
    # government & meetings
    r'council meeting|school board|committee of the whole|'
    # nature / outdoor activities (non-music)
    r'tide.pool|hatchery|meteor shower|low tide|'
    # community activities
    r'preschool|first aid|fly.tying|fly tying|circus|gem show|rock.*gem|'
    r'scrabble|carving competition|adventure show|home show|'
    # food promotions
    r'burger night|burger.*special|'
    # calendar markers — handle both straight and curly apostrophes
    r"mother[\u2019']s day|father[\u2019']s day|full moon|victoria day|"
    # performing arts non-music
    r'panel discussion|panel talk|speaker series|lecture|film festival|'
    r'cancelled)\b',
    re.I,
)


def to_live_shows_event(raw: dict, source_id: str, cfg: dict) -> dict | None:
    exclude_cats   = cfg.get("exclude_cats", set())
    exclude_cities = cfg.get("exclude_cities", set())

    # Category filter
    cats_lower = {c["name"].lower() for c in raw.get("categories", [])}
    if cats_lower & exclude_cats:
        return None

    venue_raw  = raw.get("venue") or {}
    venue_obj  = venue_raw[0] if isinstance(venue_raw, list) else venue_raw
    image_obj  = raw.get("image") or {}
    start      = raw.get("start_date", "")      # "2026-04-10 19:30:00"
    title      = html.unescape(raw.get("title", "")).strip()
    city_raw   = venue_obj.get("city") or venue_obj.get("stateprovince") or ""
    ticket_url = raw.get("url") or None

    # Non-music event filter
    if NON_MUSIC_RE.search(title):
        return None

    # City exclusion
    if exclude_cities and city_raw.strip().lower() in exclude_cities:
        return None

    date_part = start[:10] if start else None
    time_part = start[11:16] if len(start) >= 16 else None   # "19:30"

    cost = (raw.get("cost") or "").strip() or None

    img_url = None
    if isinstance(image_obj, dict):
        img_url = image_obj.get("url") or None

    venue_name = (venue_obj.get("venue") or "").strip() or cfg.get("default_venue")
    city       = normalise_city(city_raw) if city_raw.strip() else cfg.get("default_city", "")

    return {
        "id":         f"{source_id}:{raw['id']}",
        "title":      title,
        "venue":      venue_name or None,
        "city":       city,
        "date":       date_part,
        "start_time": time_part,
        "ticket_url": ticket_url,
        "price":      cost,
        "image_url":  img_url,
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


def merge(existing: list[dict], incoming: list[dict], source_ids: set[str], replace: bool) -> tuple[list[dict], int, int]:
    if replace:
        kept     = [e for e in existing if e["id"].split(":")[0] not in source_ids]
        old_ids  = {e["id"] for e in existing if e["id"].split(":")[0] in source_ids}
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
    parser = argparse.ArgumentParser(description="Scrape Tribe Events Calendar sites → events.json")
    parser.add_argument("--site",    choices=list(SITES), default=None, help="scrape one site only")
    parser.add_argument("--write",   action="store_true",  help="write results to events.json")
    parser.add_argument("--replace", action="store_true",  help="replace existing entries (implies --write)")
    args = parser.parse_args()

    if args.replace:
        args.write = True

    sites_to_run = {args.site: SITES[args.site]} if args.site else SITES

    all_incoming: list[dict] = []
    source_ids_used: set[str] = set()

    for key, cfg in sites_to_run.items():
        source_id = cfg["name"]
        source_ids_used.add(source_id)
        print(f"Fetching {source_id} …", file=sys.stderr)
        raw_events = fetch_site(cfg)
        converted  = []
        for raw in raw_events:
            ev = to_live_shows_event(raw, source_id, cfg)
            if ev and ev["title"] and ev["date"]:
                converted.append(ev)
        print(f"  {len(raw_events)} fetched, {len(converted)} kept.", file=sys.stderr)
        all_incoming.extend(converted)

    # Deduplicate within incoming (same event listed on multiple sites)
    # Key: date + first 20 chars of normalised title + venue
    # Using 20 chars catches minor title variations (e.g. "Lounge" inserted mid-title)
    seen: set[str] = set()
    deduped: list[dict] = []
    for e in all_incoming:
        t_norm  = (e["title"] or "").lower().replace(" lounge", "").replace(" upstairs", "")
        key_str = f"{e['date']}|{t_norm[:20]}|{(e['venue'] or '').lower()[:25]}"
        if key_str not in seen:
            seen.add(key_str)
            deduped.append(e)

    print(f"\nTotal incoming: {len(all_incoming)} → {len(deduped)} after dedup.", file=sys.stderr)

    data     = load_events_json()
    existing = data.get("events", [])
    print(f"{len(existing)} events currently in events.json.", file=sys.stderr)

    merged, added, replaced = merge(existing, deduped, source_ids_used, replace=args.replace)

    if args.write:
        data["events"]    = merged
        data["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        save_events_json(data)
        print(f"Written: {added} added, {replaced} replaced. Total: {len(merged)}.", file=sys.stderr)
    else:
        existing_ids = {e["id"] for e in existing}
        new_events   = [e for e in deduped if e["id"] not in existing_ids]
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
