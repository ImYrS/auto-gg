import typing as t
import os
import logging
from datetime import datetime
import re

from configobj import ConfigObj
from playwright.sync_api import sync_playwright, expect, BrowserContext, Page
from pydantic import BaseModel


class AutoGGConfig(BaseModel):
    username: str
    password: str
    sims_in_single_order: t.Literal["1", "3"]
    total_orders: int
    debug: bool = False


class AutoGG:

    def __init__(self, config: AutoGGConfig):
        self.config = config
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.firefox.launch(headless=False)
        self.context: t.Optional[BrowserContext] = None

    def run(self):
        self.context = self.browser.new_context(
            **{"storage_state": ".gg.session"} if os.path.exists(".gg.session") else {}
        )
        page = self.context.new_page()

        page.goto("https://www.giffgaff.com/orders/mgm", wait_until="networkidle")
        self._captcha_handler(page)

        if page.url.startswith("https://www.giffgaff.com/auth/login"):
            logging.info(">> Session file not exists or expired, logging in...")
            self._login(page)

        self._captcha_handler(page)

        logging.info(">> Session loaded, preparing for order process...")
        quota = self._get_quota(page)
        if quota is not None:
            ...

        for _ in range(self.config.total_orders):
            self._order_sims(page)

            # Reload the page to order more SIMs if needed
            if _ != self.config.total_orders - 1:
                page.reload(wait_until="domcontentloaded")

        self.context.storage_state(path=".gg.session")
        logging.info(">> All orders completed, session saved")
        self.browser.close()

    @staticmethod
    def _captcha_handler(page: Page):
        """
        Check if h-captcha is present and handle it.
        """
        if page.frame_locator("internal:text=\"Request unsuccessful.\"i").locator(".h-captcha").is_visible():
            page.frame_locator("internal:text=\"Request unsuccessful.\"i").locator(".h-captcha").click()
            logging.warning(">> h-captcha detected, please complete it manually in 60s!")
            try:
                # title 中包含 "giffgaff"
                expect(page).to_have_title(
                    re.compile(r"giffgaff", re.IGNORECASE),
                    timeout=60000,
                )
            except TimeoutError:
                raise TimeoutError(
                    "CAPTCHA UNRESOLVED\n"
                    "Please try again and solve the captcha manually."
                )

    def _login(self, page: Page):
        page.wait_for_load_state("networkidle", timeout=10000)
        try:
            page.get_by_role("button", name="Accept all cookies").click(timeout=1000)
        except Exception:
            pass

        logging.info(">> Logging in with username: %s", self.config.username)
        page.get_by_label("Mobile number or member name").fill(self.config.username)
        page.get_by_label("Password").fill(self.config.password)
        page.get_by_test_id("submitbtn").click()

        try:
            expect(page.get_by_role("heading", name="Confirm it's you")).to_be_visible()
            logging.warning(">> Two-factor authentication required!")
            page.evaluate("alert('Please enter verification code from SMS')")
            code = input("Please enter verification code from SMS: ")

            page.get_by_label("Enter verification code").fill(code)
            page.get_by_text("Remember me here until I log").click()
            page.get_by_role("button", name="Log in").click()
        except (AssertionError, TimeoutError):
            logging.info(">> Two-factor authentication not required, continuing...")
            pass

        try:
            page.wait_for_url("https://www.giffgaff.com/orders/mgm")
            page.wait_for_load_state("networkidle", timeout=10000)
        except TimeoutError:
            screenshot = f"error-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.png"
            page.screenshot(path=screenshot)
            raise Exception(
                "LOGIN PROCESS FAILED\n"
                "Check your credentials and try again.\n"
                f"Or report this on GitHub with the screenshot: {screenshot}"
            )

        self.context.storage_state(path=".gg.session")
        logging.info(">> Logged in successfully, session saved")

    @staticmethod
    def _get_quota(page: Page) -> t.Optional[int]:
        logging.info(">> Trying to get available SIMs quota...")
        try:
            quota_text = page.locator('text=You can order').first.inner_text()
            regex = r'You can order (\d+) more SIMs this month'
            match = re.search(regex, quota_text)
            quota: t.Optional[int] = int(match.group(1)) if match else None
        except TimeoutError:
            quota = None

        if quota is None:
            logging.warning(">> Failed to get available SIMs quota, setting as unlimited")
        else:
            logging.info(f">> Available SIMs quota: {quota}")

        return quota

    def _order_sims(self, page: Page) -> bool:
        logging.info(">> Ordering SIMs...")
        page.get_by_label("How many SIMs do you want to").select_option(self.config.sims_in_single_order)
        page.get_by_role("button", name="Send me the SIMs").click()

        try:
            # heading 中包含 The SIM you ordered is on* 且后面跟着任意字符
            expect(
                page.get_by_role(
                    "heading",
                    name=re.compile(r"The SIM[s]? you ordered (?:is|are) on*", re.IGNORECASE),
                )
            ).to_be_visible()
            logging.info(f">> {self.config.sims_in_single_order} SIM(s) ordered successfully")
            return True
        except AssertionError as e:
            logging.error(f">> Failed to order or confirm order: {e}")
            return False


def main():
    try:
        conf = ConfigObj("config.ini")
    except FileNotFoundError:
        raise FileNotFoundError(
            "CONFIG FILE NOT FOUND\n"
            "Make a copy of example.config.ini and rename it to config.ini and modify the settings."
        )

    config = AutoGGConfig(
        username=conf["auth"]["username"],
        password=conf["auth"]["password"],
        sims_in_single_order=conf["feat"]["sims_in_single_order"],
        total_orders=conf["feat"]["total_orders"],
        debug=conf["dev"]["debug"],
    )

    logging.basicConfig(
        level=logging.DEBUG if config.debug else logging.WARNING,
        format="%(asctime)s - %(filename)s:%(lineno)d - %(levelname)s: %(message)s",
    )

    AutoGG(config).run()


if __name__ == "__main__":
    main()
