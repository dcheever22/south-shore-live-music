"""
Parser for Sue P.'s recurring "LOCAL LIVE MUSIC" roundup post.

Her posts list dozens of shows in one post, one per blank-line-separated
block, usually shaped like:

    Act Name, 6-9
    Venue Name, Town

...with an occasional extra note line in between (e.g. "Hosted by X"). The
whole post is for a single night (the date in the header, e.g. "Weds 7/15") —
confirmed directly against the live page, since an earlier version of this
parser wrongly assumed "MUSIC NEXT WEEK" / "NEXT WEEK" markers shifted every
block after them forward a week, which hid that night's real shows (e.g.
Matt Silva, Electric Dreams) under a future date.

Those markers are actually small standalone 2-line teaser blocks — marker +
a bare venue name, no act or time — previewing a not-yet-confirmed future
booking at that one venue. They get dated a week out; every other block is
dated the header's night, regardless of where it falls in the post.

This structure is consistent enough to parse far more reliably than
free-form posts from random members — that's the whole reason we're scoping
the scraper down to just her.
"""

import hashlib
import re
from datetime import datetime

TIME_PATTERN = re.compile(
    r"(?P<start_hour>\d{1,2})(:(?P<start_min>\d{2}))?"
    r"(\s*-\s*(?P<end_hour>\d{1,2})(:(?P<end_min>\d{2}))?)?"
)
WEEK_MARKER_PATTERN = re.compile(r"next week", re.IGNORECASE)
HEADER_DATE_PATTERN = re.compile(r"(\d{1,2})/(\d{1,2})")

LEADING_NOISE_PATTERN = re.compile(
    r"^[\*\s]*"                          # leading "*" bullets
    r"(music\s+(today|by)\s*:?\s*)?",    # "Music today:" / "Music by"
    re.IGNORECASE,
)


def split_act_time(line):
    """Split "Act Name, 6:30-9" into ("Act Name", "6:30-9"). Only trusts a
    time found after the LAST comma — searching the whole line would also
    match digits embedded in act names (e.g. "Nineteen89, 6:30-9")."""
    if "," in line:
        left, right = line.rsplit(",", 1)
        if TIME_PATTERN.search(right):
            return left.strip(), right.strip()
    return line.strip(), None


def has_time(line):
    _, time_part = split_act_time(line)
    return time_part is not None


def _format_hour(hour, minute, is_end):
    if is_end and hour == 12:
        hour = 0  # "...-12" means midnight, not noon, for an evening show's end time
    elif hour < 12:
        hour += 12  # every show in this list is afternoon/evening
    return f"{hour:02d}:{minute or '00'}"


def extract_time_range(line):
    """Returns (start, end) as "HH:MM" strings; end is None when the source
    only gave a single time (e.g. "8:00") rather than a range ("8-11")."""
    _, time_part = split_act_time(line)
    if not time_part:
        return None, None
    match = TIME_PATTERN.search(time_part)
    if not match:
        return None, None

    start = _format_hour(int(match.group("start_hour")), match.group("start_min"), is_end=False)
    end = None
    if match.group("end_hour"):
        end = _format_hour(int(match.group("end_hour")), match.group("end_min"), is_end=True)
    return start, end


def clean_act_name(line):
    act_part, _ = split_act_time(line)
    act_part = LEADING_NOISE_PATTERN.sub("", act_part).strip(" ,-*")
    return None if act_part.strip("? ") == "" else act_part


def split_venue_line(line):
    if "," in line:
        venue, town = line.rsplit(",", 1)
        return venue.strip(), town.strip()
    return line.strip(), None


def header_reference_date(header_block, post_dt):
    match = HEADER_DATE_PATTERN.search(header_block)
    if not match:
        return post_dt.date()
    month, day = int(match.group(1)), int(match.group(2))
    year = post_dt.year
    return datetime(year, month, day).date()


def parse_block(lines, event_date):
    """Returns an event dict for one non-header block, dated event_date."""
    venue, town = split_venue_line(lines[-1])
    body_lines = lines[:-1]

    time_line_idx = next((i for i, l in enumerate(body_lines) if has_time(l)), None)

    time_str = None
    time_end_str = None
    act = None
    notes = []

    if time_line_idx is not None:
        time_str, time_end_str = extract_time_range(body_lines[time_line_idx])
        act = clean_act_name(body_lines[time_line_idx])
        notes = [l for i, l in enumerate(body_lines) if i != time_line_idx]
    else:
        notes = body_lines

    # A line like ", 6:30-8" has a time but no act text — fall back to the
    # nearest other line, preferring the one right after the time (closer to
    # the venue) over one before it, e.g. a preceding series/event name.
    if not act and time_line_idx is not None:
        after = [(i, l) for i, l in enumerate(body_lines) if i > time_line_idx and l.strip(" *")]
        before = [(i, l) for i, l in enumerate(body_lines) if i < time_line_idx and l.strip(" *")]
        candidates = after + before
        if candidates:
            fallback_idx, fallback_line = candidates[0]
            act = clean_act_name(fallback_line)
            notes = [l for i, l in enumerate(body_lines) if i != time_line_idx and i != fallback_idx]

    return {
        "band": act or None,
        "venue": venue or None,
        "town": town,
        "date": event_date.isoformat(),
        "time": time_str,
        "time_end": time_end_str,
        "notes": " / ".join(n.strip(" *") for n in notes if n.strip(" *")) or None,
        "needs_review": not (act and venue and time_str),
    }


def parse_sue_post(post):
    """Explodes one of Sue's roundup posts into a list of individual show events."""
    text = post["raw_text"]
    blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
    if not blocks:
        return []

    post_dt = datetime.fromtimestamp(post["post_time"]) if post.get("post_time") else datetime.now()
    header_date = header_reference_date(blocks[0], post_dt)
    events = []
    for block in blocks[1:]:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        if not lines:
            continue

        # A standalone marker + bare-venue teaser (no act/time) is just a
        # "check back next week" placeholder for a not-yet-confirmed future
        # booking, not a real show — skip it rather than list a nameless,
        # timeless entry.
        if WEEK_MARKER_PATTERN.search(lines[0]):
            continue

        event = parse_block(lines, header_date)
        # A stable hash of the block's own text, not a position index — an
        # index shifts (and silently duplicates in main.py's merge) whenever
        # the number of skipped/included blocks before it changes between runs.
        block_id = hashlib.sha1(block.encode()).hexdigest()[:10]
        event.update({
            "id": f"{post['id']}-{block_id}",
            "post_url": post.get("post_url"),
            "source_post_id": post["id"],
            "raw_text": block,
            "scraped_at": post.get("scraped_at"),
        })
        events.append(event)

    return events


def parse_sue_posts(posts):
    events = []
    for post in posts:
        events.extend(parse_sue_post(post))
    return events
