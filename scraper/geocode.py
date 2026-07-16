"""
Free geocoding for venue map pins, via OpenStreetMap's Nominatim.

Nominatim's usage policy (https://operations.osmfoundation.org/policies/nominatim/)
caps requests at 1/second and requires a descriptive User-Agent — both handled
here. Results are cached to geocode_cache.json (committed to the repo) so a
given venue is only ever looked up once across all future runs, not re-queried
every scrape.
"""

import json
import re
import time
from pathlib import Path

import requests

from google_maps_lookup import GoogleMapsLookup

# Nominatim can fail to match an otherwise-correct address over a unit/suite
# designator alone — "52 Ferry St Suite 3, Fall River, MA 02721" returned
# nothing, while the same address minus "Suite 3" matched immediately.
SUITE_PATTERN = re.compile(r",?\s*(suite|ste\.?|unit|apt\.?|#)\s*\w+", re.IGNORECASE)

CACHE_FILE = Path(__file__).parent / "geocode_cache.json"
USER_AGENT = "south-shore-live-music/0.1 (personal hobby project; not for redistribution)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Rough bounding box around Massachusetts (left,top,right,bottom), so a
# same-named place elsewhere in the world (e.g. "Hull" is also a city in
# England and in Quebec) doesn't win instead of the real South Shore venue.
MA_VIEWBOX = "-73.6,42.9,-69.8,41.1"

# A "precise" match (venue name or scraped address) more than this far from
# the town's own center is more likely a same-named place in the wrong town
# than the real venue — e.g. a generic name like "Pier 52" or "Himalaya
# Restaurant" turned up a match ~20-45 miles away in real testing. Roughly
# 7 miles at this latitude; reject and fall back to the town-level pin
# instead of showing a precise-looking but wrong location.
MAX_VENUE_DISTANCE_DEGREES = 0.1


def _distance_degrees(lat1, lon1, lat2, lon2):
    return ((lat1 - lat2) ** 2 + (lon1 - lon2) ** 2) ** 0.5


def _load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    return {}


def _save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _query_nominatim(query, viewbox=MA_VIEWBOX):
    params = {
        "q": query,
        "format": "json",
        "limit": 1,
        "countrycodes": "us",
        "viewbox": viewbox,
        "bounded": 1,
    }
    resp = requests.get(NOMINATIM_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=10)
    resp.raise_for_status()
    results = resp.json()
    time.sleep(1.1)  # stay under Nominatim's 1 request/second limit
    return results


def geocode_venue(venue, town, google_maps=None):
    """Looks up a precise pin for the venue itself, falling back to the town
    center if the specific place can't be found. Returns None if neither
    resolves. Cached to disk by (venue, town) so repeats are free."""
    cache = _load_cache()
    key = f"{venue}, {town}".strip(", ").lower()
    if key in cache:
        return cache[key]

    result = None

    # Look up the town's own center first (when we have a town) so every
    # "precise" candidate below can be checked against it — a match far from
    # the town center is more likely a same-named place elsewhere than the
    # real venue, and should be rejected rather than shown as if confident.
    town_center = None
    if town:
        town_matches = _query_nominatim(f"{town}, Massachusetts")
        if town_matches:
            town_center = (float(town_matches[0]["lat"]), float(town_matches[0]["lon"]))

    def _near_town(lat, lon):
        return town_center is None or _distance_degrees(lat, lon, *town_center) <= MAX_VENUE_DISTANCE_DEGREES

    if venue and town:
        matches = _query_nominatim(f"{venue}, {town}, Massachusetts")
        if matches:
            lat, lon = float(matches[0]["lat"]), float(matches[0]["lon"])
            if _near_town(lat, lon):
                result = {"lat": lat, "lon": lon, "precision": "venue", "display_name": matches[0]["display_name"]}

    # Some venue lines have no comma at all (e.g. "Plymouth Waterfront"), so
    # there's no separate town to combine it with — try the venue name alone.
    # (No town_center to validate against in this case, since town is None.)
    if not result and venue and not town:
        matches = _query_nominatim(f"{venue}, Massachusetts")
        if matches:
            result = {
                "lat": float(matches[0]["lat"]),
                "lon": float(matches[0]["lon"]),
                "precision": "venue",
                "display_name": matches[0]["display_name"],
            }

    # OpenStreetMap's business-name coverage is volunteer-maintained and
    # spotty for small local bars. Before giving up to a town-level pin, ask
    # Google Maps for the venue's actual street address, then geocode that
    # address normally — Nominatim is reliable for real addresses even when
    # it can't find a business by name. See google_maps_lookup.py for why
    # this step can fail silently (it's scraping, not an official API).
    if not result and venue and town and google_maps:
        # find_address() returns a full "street, city, MA zip" line (it does
        # its own follow-up search to get there — see google_maps_lookup.py),
        # so it's used as-is here rather than appending the town again.
        address = google_maps.find_address(venue, town)
        if address:
            address = SUITE_PATTERN.sub("", address)
            # Massachusetts has real town/county name collisions —
            # "Plymouth" is both a town AND a county (Brockton is in Plymouth
            # COUNTY), so even a fully zip-qualified address can occasionally
            # lose to a same-named street elsewhere in the county on a plain
            # statewide search. A tight box around the town's own center
            # (rather than all of MA) rules that out regardless of which
            # "Plymouth" Nominatim's text matching prefers.
            tight_viewbox = MA_VIEWBOX
            if town_center:
                lat0, lon0 = town_center
                # Must be at least MAX_VENUE_DISTANCE_DEGREES — a real
                # Plymouth address (the actual downtown/harbor Court St) once
                # tested ~0.09 degrees from Nominatim's own town-centroid
                # point, so a tighter search box than the acceptance
                # threshold rejected it before the distance check ever ran.
                pad = MAX_VENUE_DISTANCE_DEGREES + 0.02
                tight_viewbox = f"{lon0 - pad},{lat0 + pad},{lon0 + pad},{lat0 - pad}"

            matches = _query_nominatim(address, viewbox=tight_viewbox)
            if matches:
                lat, lon = float(matches[0]["lat"]), float(matches[0]["lon"])
                if _near_town(lat, lon):
                    result = {
                        "lat": lat,
                        "lon": lon,
                        "precision": "venue",
                        "display_name": matches[0]["display_name"],
                    }

    if not result and town_center:
        result = {"lat": town_center[0], "lon": town_center[1], "precision": "town", "display_name": town}

    cache[key] = result
    _save_cache(cache)
    return result


def geocode_events(events):
    with GoogleMapsLookup() as google_maps:
        for event in events:
            location = geocode_venue(event.get("venue"), event.get("town"), google_maps)
            if location:
                event["lat"] = location["lat"]
                event["lon"] = location["lon"]
                event["geocode_precision"] = location["precision"]
            else:
                event["lat"] = None
                event["lon"] = None
                event["geocode_precision"] = None
    return events
