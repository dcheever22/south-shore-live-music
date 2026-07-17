"""
Orchestrates one scrape run: fetch -> parse -> merge with existing data ->
write site/events.json.

Run locally with:
    python main.py
The GitHub Actions workflow runs this on a schedule.
"""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from geocode import geocode_events
from parse_sue import parse_sue_posts
from photo_lookup import add_photos
from scrape import scrape

OUTPUT_FILE = Path(__file__).parent.parent / "site" / "events.json"
KEEP_PAST_DAYS = 3  # drop events older than this so the file doesn't grow forever


def load_output():
    if OUTPUT_FILE.exists():
        data = json.loads(OUTPUT_FILE.read_text())
        if isinstance(data, dict):
            return data.get("last_updated_at"), data["events"]
        return None, data  # older bare-array format
    return None, []


def _comparable(events):
    # Keyed by id (order-independent) and stripped of scraped_at, which is
    # stamped fresh on every scrape regardless of whether the underlying
    # post content actually changed — otherwise every run would look like a
    # change even when nothing is.
    return {e["id"]: {k: v for k, v in e.items() if k != "scraped_at"} for e in events}


def main():
    previous_updated_at, previous_events = load_output()
    existing = {e["id"]: e for e in previous_events}

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

    # "Last updated" means last time the actual show data changed, not last
    # time we happened to check — so only bump it (and only write/commit at
    # all) when something meaningful is actually different. Otherwise every
    # run would touch the file (scraped_at always differs) and trigger a
    # deploy even on a quiet check that found nothing new.
    if _comparable(events) == _comparable(previous_events):
        print("No changes since last run — leaving events.json untouched.")
        return

    output = {
        "last_updated_at": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"Wrote {len(events)} events to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
