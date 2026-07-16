"""
Orchestrates one scrape run: fetch -> parse -> merge with existing data ->
write site/events.json.

Run locally with:
    python main.py
The GitHub Actions workflow runs this on a schedule.
"""

import json
from datetime import date, timedelta
from pathlib import Path

from geocode import geocode_events
from parse_sue import parse_sue_posts
from photo_lookup import add_photos
from scrape import scrape

OUTPUT_FILE = Path(__file__).parent.parent / "site" / "events.json"
KEEP_PAST_DAYS = 3  # drop events older than this so the file doesn't grow forever


def load_existing():
    if OUTPUT_FILE.exists():
        return {e["id"]: e for e in json.loads(OUTPUT_FILE.read_text())}
    return {}


def main():
    existing = load_existing()

    raw_posts = scrape()
    parsed = parse_sue_posts(raw_posts)
    geocode_events(parsed)
    add_photos(parsed)

    # Replace every event from each re-scraped source post wholesale, rather
    # than merging in by id forever — otherwise a show that no longer parses
    # out this run (she edited the post, or a parser fix changes the set of
    # blocks) leaves its old entry stranded in the file indefinitely.
    rescraped_post_ids = {event["source_post_id"] for event in parsed}
    existing = {
        event_id: event
        for event_id, event in existing.items()
        if event.get("source_post_id") not in rescraped_post_ids
    }

    for event in parsed:
        existing[event["id"]] = event

    cutoff = (date.today() - timedelta(days=KEEP_PAST_DAYS)).isoformat()
    events = sorted(
        (e for e in existing.values() if (e["date"] or "9999-99-99") >= cutoff),
        key=lambda e: e["date"] or "9999-99-99",
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(events, indent=2))
    print(f"Wrote {len(events)} events to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
