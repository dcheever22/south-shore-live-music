"""
Pulls Sue P.'s current weekly "LOCAL LIVE MUSIC" roundup post from
both the group and her personal page, by reading the feed data Facebook
sends the page as it loads and scrolls (more reliable here than parsing the
rendered page directly).

Requires session.json (see login.py). Run directly to sanity-check output:
    python scrape.py
"""

import json
import sys
import time
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

SESSION_FILE = "session.json"
GROUP_URL = "https://www.facebook.com/groups/2495687603787814"
PROFILE_URL = "https://www.facebook.com/sue.petersen3"
SCROLL_ROUNDS = 15          # how many times to scroll per run (more = further back in history)
SCROLL_PAUSE_SECONDS = 2.5  # let each scroll's requests come back before scrolling again

# Random-member posts turned out to be too free-form to parse reliably. Sue
# P. posts a consistently-structured daily roundup of the whole area's
# live music, which parse_sue.py can parse with far higher accuracy — so we
# scope the scraper down to just her instead of every post in the group. She
# posts the same roundup to both the group and her own page (not always
# identical), so we check both and keep whichever copy is newest. She also
# posts plenty of unrelated things, so author alone isn't a strong enough
# filter — we also require this specific roundup's signature phrase.
TARGET_AUTHOR = "Sue Petersen"
# Her header wording drifts week to week — confirmed in practice: one week
# it's "My list for LOCAL LIVE MUSIC Weds 7/15", another it's "My LOCAL MUSIC
# list for Thurs 7/16" (word order flipped, "live" dropped entirely). An
# exact-phrase match broke on that second version and silently kept showing
# stale data. Requiring both words present anywhere, not an exact phrase,
# survives that kind of natural rewording.
ROUNDUP_SIGNATURE_WORDS = ("local", "music")


def extract_stories(payload, out):
    """
    Recursively pull group-feed story objects out of one parsed GraphQL
    response. Facebook's GraphQL schema is undocumented and can change
    without notice — if this starts returning nothing, capture a fresh
    response (see README) and re-check this structure.
    """
    if isinstance(payload, dict):
        message = payload.get("message")
        text = message.get("text") if isinstance(message, dict) else None
        # Match wherever "post_id" and a populated "message" co-occur, regardless
        # of nesting depth — reposted/shared stories bury the real content an
        # extra level down (under "attached_story"), one level deeper than a
        # normal post, and hardcoding a single fixed path missed those.
        if "post_id" in payload and text:
            actors = payload.get("actors") or []
            out.append({
                "id": str(payload.get("post_id")),
                "post_url": payload.get("wwwURL") or payload.get("permalink_url"),
                "author": actors[0].get("name") if actors else None,
                "raw_text": text,
                "post_time": payload.get("creation_time"),
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
        for value in payload.values():
            extract_stories(value, out)
    elif isinstance(payload, list):
        for item in payload:
            extract_stories(item, out)


def handle_response(response, collected):
    if "graphql" not in response.url.lower():
        return
    try:
        body = response.text()
    except Exception:
        return
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        extract_stories(payload, collected)


def is_roundup_post(post):
    text = (post.get("raw_text") or "").lower()
    return post.get("author") == TARGET_AUTHOR and all(word in text for word in ROUNDUP_SIGNATURE_WORDS)


def load_feed(page, url):
    page.goto(url, wait_until="load")

    if "login" in page.url or "checkpoint" in page.url:
        print("Session expired or checkpointed — re-run login.py", file=sys.stderr)
        sys.exit(1)

    time.sleep(4)
    for _ in range(SCROLL_ROUNDS):
        page.mouse.wheel(0, 4000)
        time.sleep(SCROLL_PAUSE_SECONDS)


def scrape():
    collected = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=SESSION_FILE, viewport={"width": 1280, "height": 2000})
        page = context.new_page()
        page.on("response", lambda r: handle_response(r, collected))

        for url in (GROUP_URL, PROFILE_URL):
            load_feed(page, url)

        browser.close()

    deduped = {post["id"]: post for post in collected if post.get("id")}
    matches = [post for post in deduped.values() if is_roundup_post(post)]
    if not matches:
        return []

    # She deletes last week's roundup when she posts a new one, so in the rare
    # case both the group and her page have a match, the newest is the live one.
    latest = max(matches, key=lambda p: p.get("post_time") or 0)
    return [latest]


if __name__ == "__main__":
    print(json.dumps(scrape(), indent=2))
