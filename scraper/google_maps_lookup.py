"""
Best-effort street-address lookup via Google Maps search, used only when
Nominatim can't find a venue by name — OpenStreetMap's business/POI coverage
is volunteer-maintained and spotty for small local bars, while Google's is
much more complete.

This is explicitly against Google's Terms of Service, same category of risk
as the Facebook scraping elsewhere in this project. Google's bot defenses
are generally tougher, especially from datacenter IPs like GitHub Actions
runners, so this can start failing or get blocked at any time. That's fine —
every call here is wrapped so a failure just falls through to the existing
town-center fallback in geocode.py rather than breaking the pipeline.

We only scrape an ADDRESS here, not coordinates — the address then goes
through Nominatim's normal structured geocoding (geocode.py), which is
reliable for real street addresses even when it can't find a business by
name.
"""

import re
from urllib.parse import quote

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# Matches a US street address line like "48 George Washington Blvd, Hull, MA 02045".
ADDRESS_LINE_PATTERN = re.compile(r"^\d+\s+.+,\s*[A-Za-z\s]+,\s*[A-Z]{2}\s*\d{5}$")


def _parse_result_item(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return None

    is_closed = "permanently closed" in text.lower()
    address = None
    for line in lines:
        if "·" not in line:
            continue
        lower = line.lower()
        if "open" in lower or "closed" in lower or "am)" in lower or "pm)" in lower:
            continue
        parts = [p.strip() for p in line.split("·") if p.strip() and any(c.isalnum() for c in p)]
        if parts:
            address = parts[-1]
            break

    return {"name": lines[0], "address": address, "closed": is_closed}


class GoogleMapsLookup:
    """Reuses one browser across many lookups in a single scrape run instead
    of paying browser-launch cost per venue. Use as a context manager."""

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

    def _search(self, query_text, town):
        browser = self._ensure_browser()
        context = browser.new_context(user_agent=USER_AGENT, viewport={"width": 1280, "height": 1600})
        page = context.new_page()
        page.goto(f"https://www.google.com/maps/search/{quote(f'{query_text} {town} MA')}", wait_until="load", timeout=15000)
        page.wait_for_timeout(3000)
        return context, page

    def find_address(self, venue, town):
        """Returns a best-guess street address string, or None on any failure
        (blocked, no results, everything closed, unexpected page structure)."""
        try:
            context, page = self._search(venue, town)

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
                    return None

                # The results list often only shows a truncated address (no
                # city/zip) — e.g. Sue wrote "Himalaya Restaurant" but the
                # real name is "The Himalaya Restaurant and Bar", and only
                # searching that exact discovered name resolved to a single
                # confident page with the full address. Massachusetts has
                # real town/county name collisions (Brockton is in Plymouth
                # COUNTY, so a bare "Plymouth" match can silently resolve
                # there instead of Plymouth the town), and the full address
                # is what lets Nominatim disambiguate that correctly.
                context2, page2 = self._search(candidate_name, town)
                address = self._address_from_detail_page(page2)
                context2.close()
                return address

            # When Google is confident there's only one real match, it skips
            # the results list entirely and jumps straight to that place's own
            # detail page instead — no div[role="feed"] ever appears, so the
            # list-parsing path above finds nothing even though the address is
            # right there on the page.
            address = self._address_from_detail_page(page)
            context.close()
            return address
        except Exception:
            return None

    def _address_from_detail_page(self, page):
        main_panel = page.locator('div[role="main"]')
        if main_panel.count() == 0:
            return None

        text = main_panel.first.inner_text()
        if "permanently closed" in text.lower():
            return None

        for line in text.split("\n"):
            line = line.strip()
            if ADDRESS_LINE_PATTERN.match(line):
                return line
        return None
