"""
One-time (or occasional) interactive login.

Run this locally:
    python login.py

A real browser window opens to mbasic.facebook.com. Log in there by hand
with the THROWAWAY Facebook account (solve any 2FA/checkpoint prompts as
they appear). Once you can see the group's feed, come back to this
terminal and press Enter. The authenticated session gets saved to
session.json, which scrape.py (and the GitHub Action) will reuse.

session.json is a credential — it is gitignored and must never be committed.
Re-run this script whenever scrape.py starts reporting login failures.
"""

from playwright.sync_api import sync_playwright

SESSION_FILE = "session.json"
LOGIN_URL = "https://mbasic.facebook.com/"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL)

        print("\nA browser window has opened.")
        print("1. Log in with the throwaway Facebook account.")
        print("2. Solve any 2FA / checkpoint / 'save browser' prompts.")
        print("3. Once you can see a Facebook feed, return here.")
        input("Press Enter when you're logged in... ")

        context.storage_state(path=SESSION_FILE)
        browser.close()
        print(f"Saved session to {SESSION_FILE}.")


if __name__ == "__main__":
    main()
