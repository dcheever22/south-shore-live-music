"""
Best-effort photo + link lookup for event cards: an artist photo when one can
be found (else a photo of the venue instead), and a link to the venue's own
page (its website, or its Google Maps listing) instead of Sue's Facebook post.

Two different free sources, because Google's plain Search/Images product
blocks automated traffic almost immediately — tested live, it redirects
straight to a CAPTCHA wall ("/sorry/") on the very first request. Google Maps
and Bing did not show that behavior in testing:
  - Artist photos: Bing Image Search (no key required).
  - Venue photo/website/maps link: all pulled from one visit to the venue's
    own Google Maps listing, since Maps has been reliable throughout this
    project.

There's no automatic way to confirm a Bing image search result is actually a
photo of this specific small local band rather than an unrelated same-named
result — that's a real limitation, not just a formality. Every photo record
keeps its source ("artist" vs "venue") so this is inspectable later, and
venue photos (high-confidence, since we already validated that venue's
location during geocoding) are preferred whenever the artist search comes up
empty.

Images are downloaded and cached locally under site/photos/ rather than
hotlinked — thumbnail URLs from either source can change or expire without
notice.
"""

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote

import requests

from google_maps_lookup import _parse_result_item

CACHE_FILE = Path(__file__).parent / "photo_cache.json"
VENUE_CACHE_FILE = Path(__file__).parent / "venue_info_cache.json"
PHOTOS_DIR = Path(__file__).parent.parent / "site" / "photos"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# A bare domain line on a Maps listing, e.g. "drifthull.com". Requires an
# alphabetic TLD so it doesn't false-positive on things like a "4.4" rating.
WEBSITE_LINE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9-]*(\.[a-zA-Z0-9-]+)*\.[a-zA-Z]{2,}$")

# Some venues list just the bare social-network domain (e.g. "facebook.com"
# with no page path at all) as their "website" on Maps — seen in testing for
# a real venue. Third-party reservation/ordering widgets on the page (e.g. a
# "Reserve with Tock" button) can also false-positive-match as if they were
# the venue's own site — seen in testing too ("exploretock.com" for a venue
# that doesn't use Tock at all as far as we could tell). None of these are a
# useful venue-specific link, so treat them as absent.
USELESS_BARE_DOMAINS = {
    "facebook.com", "www.facebook.com",
    "instagram.com", "www.instagram.com",
    "exploretock.com", "www.exploretock.com",
    "opentable.com", "www.opentable.com",
    "resy.com", "www.resy.com",
    "doordash.com", "www.doordash.com",
    "grubhub.com", "www.grubhub.com",
    "ubereats.com", "www.ubereats.com",
    "toasttab.com", "www.toasttab.com",
    "yelp.com", "www.yelp.com",
    "spotapps.co", "www.spotapps.co",
}


def _load_json(path):
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True))


def _download_image(url, key):
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": USER_AGENT})
        if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
            return None
        ext = resp.headers["content-type"].split("/")[-1].split(";")[0]
        ext = "jpg" if ext == "jpeg" else ext
        filename = f"{hashlib.sha1(key.encode()).hexdigest()[:12]}.{ext}"
        PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
        (PHOTOS_DIR / filename).write_bytes(resp.content)
        return f"photos/{filename}"
    except requests.RequestException:
        return None


class PhotoLookup:
    """Reuses one browser across many lookups in a single scrape run. Use as
    a context manager."""

    def __init__(self):
        self._playwright = None
        self._browser = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    def close(self):
        if self._browser:
            self._browser.close()
            self._browser = None
        if self._playwright:
            self._playwright.stop()
            self._playwright = None

    def _ensure_browser(self):
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        return self._browser

    def _new_page(self):
        browser = self._ensure_browser()
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 1600})
        return context, context.new_page()

    def find_artist_photo_url(self, band_name, town):
        try:
            context, page = self._new_page()
            query = f"{band_name} band {town or ''}".strip()
            page.goto(f"https://www.bing.com/images/search?q={quote(query)}", wait_until="load", timeout=15000)
            page.wait_for_timeout(1500)

            imgs = page.locator("img.mimg")
            url = None
            for i in range(min(imgs.count(), 5)):
                src = imgs.nth(i).get_attribute("src")
                if src and src.startswith("http"):
                    url = src
                    break
            context.close()
            return url
        except Exception:
            return None

    def _search_maps(self, query_text, town):
        context, page = self._new_page()
        query = f"{query_text} {town or ''} MA".strip()
        page.goto(f"https://www.google.com/maps/search/{quote(query)}", wait_until="load", timeout=15000)
        page.wait_for_timeout(2500)
        return context, page

    def _info_from_detail_page(self, page):
        info = {"photo_url": None, "website": None, "maps_url": None}
        main_panel = page.locator('div[role="main"]')
        if main_panel.count() == 0:
            return info

        imgs = main_panel.first.locator("img")
        for i in range(imgs.count()):
            src = imgs.nth(i).get_attribute("src") or ""
            if "gps-cs-s" in src or "gps-proxy" in src:
                info["photo_url"] = src
                break

        # A listing sometimes shows two domain-like lines — a reservation
        # widget (e.g. "spotapps.co") followed by the venue's actual website
        # right before the phone number. Keep matching through the whole
        # page rather than stopping at the first hit, so the real website
        # (whichever comes last) wins over an ordering/reservation widget.
        text = main_panel.first.inner_text()
        for line in text.split("\n"):
            line = line.strip()
            if WEBSITE_LINE_PATTERN.match(line) and line.lower() not in USELESS_BARE_DOMAINS:
                info["website"] = f"https://{line}"

        # Google Maps doesn't change the browser URL to a /place/... permalink
        # even for a single confident match (tested live) — the search URL
        # itself is the best link available, and it reliably resolves back to
        # the same result.
        info["maps_url"] = page.url
        return info

    def find_venue_info(self, venue, town):
        """Photo, website, and a Maps link, all from one visit — or two, if
        the first only turns up an ambiguous results list rather than a
        single confident match (see google_maps_lookup.py's find_address for
        why re-searching the exact discovered name usually resolves that)."""
        try:
            context, page = self._search_maps(venue, town)

            feed = page.locator('div[role="feed"]')
            if feed.count() > 0:
                items = feed.first.locator(":scope > div")
                candidate_name = None
                for i in range(items.count()):
                    parsed = _parse_result_item(items.nth(i).inner_text())
                    if parsed and not parsed["closed"] and parsed["address"]:
                        candidate_name = parsed["name"]
                        break
                context.close()

                if not candidate_name:
                    return {"photo_url": None, "website": None, "maps_url": None}

                context2, page2 = self._search_maps(candidate_name, town)
                info = self._info_from_detail_page(page2)
                context2.close()
                return info

            info = self._info_from_detail_page(page)
            context.close()
            return info
        except Exception:
            return {"photo_url": None, "website": None, "maps_url": None}


def photo_for_event(event, lookup, photo_cache, venue_cache):
    """Returns (relative_photo_path, source, link) for an event. Venue info
    (photo/website/maps link) is looked up for every venue regardless of
    whether an artist photo is found, since the link should be available on
    every card, not just ones where the artist search came up empty."""
    venue = event.get("venue")
    venue_key = f"{(venue or '').lower()}|{(event.get('town') or '').lower()}"

    if venue and venue_key not in venue_cache:
        info = lookup.find_venue_info(venue, event.get("town"))
        venue_cache[venue_key] = {
            "photo": _download_image(info["photo_url"], f"venue:{venue_key}") if info["photo_url"] else None,
            "website": info["website"],
            "maps_url": info["maps_url"],
        }
    venue_info = venue_cache.get(venue_key, {})
    link = venue_info.get("website") or venue_info.get("maps_url") or event.get("post_url")

    band = event.get("band")
    if band and band.strip("? ") and not event.get("needs_review"):
        artist_key = f"artist:{band.lower()}"
        if artist_key not in photo_cache:
            url = lookup.find_artist_photo_url(band, event.get("town"))
            photo_cache[artist_key] = _download_image(url, artist_key) if url else None
        if photo_cache[artist_key]:
            return photo_cache[artist_key], "artist", link

    if venue_info.get("photo"):
        return venue_info["photo"], "venue", link

    return None, None, link


def add_photos(events):
    photo_cache = _load_json(CACHE_FILE)
    venue_cache = _load_json(VENUE_CACHE_FILE)
    with PhotoLookup() as lookup:
        for event in events:
            photo, source, link = photo_for_event(event, lookup, photo_cache, venue_cache)
            event["photo"] = photo
            event["photo_source"] = source
            event["link"] = link
    _save_json(CACHE_FILE, photo_cache)
    _save_json(VENUE_CACHE_FILE, venue_cache)
    return events
