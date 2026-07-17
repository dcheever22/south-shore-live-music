# South Shore Live Music

Auto-updated map and listing of live music on Massachusetts's South Shore, sourced from Sue P.'s weekly "LOCAL LIVE MUSIC" roundup post (she posts it to both the [South Shore Live Music](https://www.facebook.com/groups/2495687603787814/) Facebook group and her [personal page](https://www.facebook.com/sue.petersen3)).

## How it works

- `scraper/scrape.py` logs into Facebook (via a **throwaway account**) and reads the group and her page while scrolling through recent posts.
- It only keeps posts by her that contain the phrase "local live music" — she posts plenty of other things, and posts the same roundup to both places (usually identical), so we scope tightly to that one post and use whichever copy is newest.
- `scraper/parse_sue.py` is purpose-built for her specific format: one block per show (act, time, venue, town), separated by blank lines, with "NEXT WEEK" markers that shift the reference date forward. This is far more reliable than trying to parse arbitrary member posts, which is why the scraper is scoped to just her.
- `scraper/geocode.py` looks up a precise pin for each venue via OpenStreetMap's free Nominatim geocoder, falling back to the town's center if the specific place can't be found. Results are cached to `scraper/geocode_cache.json` (committed to the repo) so a venue is only ever looked up once.
- Anything the parser or geocoder can't resolve confidently is flagged (`needs_review` / no map pin) rather than silently dropped or guessed wrong.
- `.github/workflows/scrape.yml` runs the scraper every 6 hours for free on GitHub Actions and commits the updated `site/events.json`.
- `site/` is a plain static site (no build step, uses Leaflet + free OpenStreetMap tiles for the map) that reads `events.json` and renders both a map and a list. Host it free via GitHub Pages.

**Important:** scraping Facebook is against its Terms of Service. Using a throwaway account (not your personal one) limits the blast radius if Facebook restricts or bans it — you rejoin with a new throwaway account and re-run the login step. Facebook can also change its site at any time in ways that break this; if `events.json` stops updating, check the Actions tab for failures first.

## One-time setup

1. Create the throwaway Facebook account and join the group with it.
2. Locally, install dependencies and Playwright's browser:
   ```
   cd scraper
   pip install -r requirements.txt
   playwright install chromium
   ```
3. Log in and save a session:
   ```
   python login.py
   ```
   This opens a real browser window — log into the throwaway account there (solving any 2FA/checkpoint prompts), then press Enter in the terminal. This creates `session.json`.
4. Test the scraper locally:
   ```
   python main.py
   ```
   Check `site/events.json` for real output.
5. Push this repo to GitHub.
6. Add the session as a GitHub Actions secret named `FB_SESSION`:
   ```
   base64 < scraper/session.json | pbcopy
   ```
   Paste that into a new repository secret at **Settings → Secrets and variables → Actions → New repository secret**, named `FB_SESSION`.
7. Enable GitHub Pages: **Settings → Pages → Source: Deploy from a branch**, branch `main`, folder `/site`.
8. Trigger the workflow once manually from the **Actions** tab (`Scrape South Shore Live Music` → **Run workflow**) to confirm it commits real data and the Pages site picks it up.

## Ongoing maintenance

- **Session expires**: the scheduled run will start failing (visible in the Actions tab). Re-run `python login.py` locally and update the `FB_SESSION` secret with the new `session.json`.
- **Sue changes her post format**: re-check the block structure in `scraper/parse_sue.py` against a fresh real post (see the comments at the top of that file for the expected shape).
- **A venue's pin is missing or wrong**: check `scraper/geocode_cache.json` — delete that venue's entry to force a fresh lookup on the next run. Some very informal venue descriptions (e.g. "Plymouth Waterfront") aren't real OpenStreetMap places and will never resolve to an exact pin; the list still shows them, just without a marker.
- **Scraper breaks after a Facebook change**: check `scraper/scrape.py` for what it expects to find and adjust as needed.
