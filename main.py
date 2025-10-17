# main.py
import sys
import re
import argparse
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from config import settings
from utils import logger, RecoverableError


retry_click = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    retry=retry_if_exception_type(RecoverableError),
)


@retry_click
def safe_click(locator):
    try:
        locator.wait_for(state="visible", timeout=settings.action_timeout_ms)
        locator.click(timeout=settings.action_timeout_ms)
    except Exception as e:
        raise RecoverableError(str(e))


def _dismiss_banners(page):
    """Dismiss cookie / country / newsletter banners if present."""
    # common button texts
    candidates = [
        "Accept",
        "Accept All",
        "I Accept",
        "I Agree",
        "Got it",
        "Allow all",
        "OK",
        "Close",
    ]
    for txt in candidates:
        try:
            btn = page.get_by_role(
                "button", name=re.compile(rf"^{re.escape(txt)}$", re.I)
            )
            if btn.count() > 0:
                safe_click(btn.first)
        except Exception:
            pass


def _open_search(page):
    """
    Nike’s search is often behind a search icon that opens an overlay.
    Try the obvious role/name combos, then a generic placeholder fallback.
    """
    # Try a visible Search button/icon
    try:
        btn = page.get_by_role("button", name=re.compile("search", re.I))
        if btn.count() > 0:
            safe_click(btn.first)
    except Exception:
        pass

    # Focus the search textbox (searchbox role or placeholder)
    # Many storefronts expose role=searchbox; fallback to placeholder contains “Search”
    search_locators = [
        page.get_by_role("searchbox"),
        page.get_by_placeholder(re.compile("search", re.I)),
        page.locator("input[type='search']"),
        page.locator("input[aria-label*='Search' i]"),
    ]
    for loc in search_locators:
        try:
            if loc.count() > 0:
                loc.first.wait_for(state="visible", timeout=settings.action_timeout_ms)
                return loc.first
        except Exception:
            continue
    raise RuntimeError("Could not find Nike search input.")


def _submit_search(search_input, query):
    search_input.fill(query, timeout=settings.action_timeout_ms)
    # submit: Enter key is usually wired
    search_input.press("Enter", timeout=settings.action_timeout_ms)


def _first_product_card(page):
    """
    Prefer Nike's data-testids. Fallbacks included.
    """
    selectors = [
        "[data-testid='product-card']",
        "article [data-testid='product-card']",
        "li [data-testid='product-card']",
        "article, li, div",  # last-resort scan
    ]

    # Wait specifically for any price to appear (faster feedback on success pages)
    try:
        page.wait_for_selector("[data-testid='product-price']", timeout=15000)
    except Exception:
        # Still try cards (maybe 'See Price in Bag' etc.)
        pass

    # 1) Fast path: first product-card that contains a product-price
    cards = page.locator("[data-testid='product-card']")
    if cards.count() > 0:
        limit = min(cards.count(), 24)
        for i in range(limit):
            card = cards.nth(i)
            price = card.locator("[data-testid='product-price']")
            if price.count() > 0:
                return card

    # 2) Fallback: scan generic containers for a descendant price
    for sel in selectors:
        nodes = page.locator(sel)
        if nodes.count() == 0:
            continue
        limit = min(nodes.count(), 24)
        for i in range(limit):
            node = nodes.nth(i)
            if node.locator("[data-testid='product-price']").count() > 0:
                return node

    raise RuntimeError("Could not find a product card with a visible price (Nike).")


def _extract_title_and_price(card):
    # Title: common testid; fallbacks provided
    title_candidates = [
        "[data-testid='product-card__title']",
        "a[aria-label]:visible",
        "h3:visible, h2:visible, h1:visible",
        "a:visible",
    ]
    title_text = None
    for tc in title_candidates:
        loc = card.locator(tc)
        if loc.count() > 0:
            try:
                t = loc.first.inner_text().strip()
                if t:
                    title_text = t
                    break
            except Exception:
                pass

    # Price: preferred testid
    price_loc = card.locator("[data-testid='product-price']")
    if price_loc.count() == 0:
        # Some regions show “See Price in Bag”. Surface that if present.
        bag_price = card.get_by_text("Price in Bag", exact=False)
        if bag_price.count() > 0:
            return title_text or "(unknown title)", "Price in Bag"
        raise RuntimeError("Found a product card but no [data-testid='product-price'].")

    price_text = price_loc.first.inner_text().strip()
    return title_text or "(unknown title)", price_text


def run(query: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=settings.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        page = context.new_page()
        page.set_default_navigation_timeout(settings.nav_timeout_ms)
        page.set_default_timeout(settings.action_timeout_ms)

        try:
            page.goto(settings.base_url)
            _dismiss_banners(page)

            # Open search and submit query
            search_input = _open_search(page)
            _submit_search(search_input, query)

            # Wait for results to load (look for anything price-looking)
            # Wait for results grid and at least one product-price node
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_selector("[data-testid='product-card']", timeout=20000)
            page.wait_for_selector("[data-testid='product-price']", timeout=20000)

            # Identify the first “card” that has a price
            card = _first_product_card(page)
            title, price = _extract_title_and_price(card)

            msg = f'Success! First result for "{query}" is "{title}" priced at {price}'
            print(msg)
            return msg

        except PWTimeout as te:
            err = f"Timeout while interacting with Nike: {te}"
            print(err)
            return err
        except Exception as e:
            err = f"Run failed: {e}"
            print(err)
            return err
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nike search + price robot")
    parser.add_argument("--query", default="Men's Pegasus", help="Search query")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    args = parser.parse_args()

    if args.headless:
        settings.headless = True

    sys.exit(0 if run(args.query).startswith("Success!") else 1)
