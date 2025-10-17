import sys
import argparse
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from config import settings
from utils import logger, RecoverableError, retry_click

# Fixed, deterministic flow on SauceDemo: login → read product price
# Product defaults to "Sauce Labs Backpack" but is CLI-configurable.

LOGIN_INPUT_USER = "[data-test='username']"
LOGIN_INPUT_PASS = "[data-test='password']"
LOGIN_BUTTON = "[data-test='login-button']"
PRODUCT_TITLE = ".inventory_item_name"
PRODUCT_CARD = ".inventory_item"
PRODUCT_PRICE = ".inventory_item_price"


@retry_click
def safe_click(locator):
    try:
        locator.wait_for(state="visible", timeout=settings.action_timeout_ms)
        locator.click(timeout=settings.action_timeout_ms)
    except Exception as e:
        raise RecoverableError(str(e))


def run(product_name: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=settings.headless)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_navigation_timeout(settings.nav_timeout_ms)
        page.set_default_timeout(settings.action_timeout_ms)


        try:
            logger.info("Navigating to login page…")
            page.goto(settings.base_url)


            logger.info("Typing credentials…")
            page.locator(LOGIN_INPUT_USER).fill(settings.username)
            page.locator(LOGIN_INPUT_PASS).fill(settings.password)
            safe_click(page.locator(LOGIN_BUTTON))


            logger.info("Waiting for inventory page…")
            page.wait_for_selector(PRODUCT_CARD)


            logger.info("Searching for product: %s", product_name)
            items = page.locator(PRODUCT_CARD)
            count = items.count()
            for i in range(count):
                card = items.nth(i)
                title = card.locator(PRODUCT_TITLE).inner_text()
                if title.strip().lower() == product_name.strip().lower():
                    price = card.locator(PRODUCT_PRICE).inner_text()
                    msg = f'Success! Product "{title}" found at price: {price}'
                    print(msg)
                    return msg


            raise RuntimeError(
                f"Product not found: '{product_name}'. Available: " +
                ", ".join(items.nth(i).locator(PRODUCT_TITLE).inner_text() for i in range(count))
            )
        except PWTimeout as te:
            err = f"Timeout while interacting with the page: {te}"
            logger.error(err)
            print(err)
            return err
        except Exception as e:
            err = f"Run failed: {e}"
            logger.error(err)
            print(err)
            return err
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Core Robot Driver")
    parser.add_argument("--product", default="Sauce Labs Backpack", help="Exact product name to find")
    parser.add_argument("--headless", action="store_true", help="Run headless")
    args = parser.parse_args()


    # Allow CLI to override headless quickly
    if args.headless:
        from config import settings as s
        s.headless = True


    sys.exit(0 if run(args.product).startswith("Success!") else 1)