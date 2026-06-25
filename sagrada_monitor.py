"""
Sagrada Familia Ticket Availability Monitor
------------------------------------------------
This script uses Selenium WebDriver to monitor ticket availability on the
official Sagrada Família ticket portal (https://tickets.sagradafamilia.org).
It navigates to the “individual ticket” page for the basilica and checks
whether tickets are available for specific dates.  When a previously sold‑
out date becomes available, the script can trigger a notification (for
example, by sending an e‑mail).

Usage notes
-----------
* Requirements:
  - Python 3.8 or later.
  - Selenium (`pip install selenium`).
  - Chrome or Chromium browser and a compatible `chromedriver` in your
    system PATH.  On Ubuntu you can install it via `sudo apt install
    chromium-driver`.
  - Optionally, an SMTP account configured in environment variables for
    sending e‑mails.

* Configuration:
  - Edit the `TARGET_DATES` list to include the visit dates you want to
    monitor.  The dates must be specified in ISO format (YYYY‑MM‑DD).
  - Set `CHECK_INTERVAL` to control how often (in seconds) the script
    refreshes the site and checks availability.

* Notification:
  - The script includes a `send_email` function that uses SMTP to send
    alerts.  To enable e‑mail notifications, set the environment variables
    `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USER`, `EMAIL_PASSWORD`, and
    `EMAIL_TO`.  If these variables are not defined, the script will simply
    print a message to the console when tickets become available.

How it works
------------
The script repeatedly performs the following steps:

1. Launch a headless browser session with Selenium.
2. Navigate to the Sagrada Família individual ticket page.
3. Accept cookies to ensure the calendar renders correctly.
4. Scroll to the calendar section and navigate to the month of each
   target date.  The site only allows booking roughly two months in
   advance, as stated in the official FAQ【142822578540595†screenshot】.
5. Attempt to click the cell representing the target date.  If the
   element is disabled or not clickable (indicating no availability), the
   script catches the exception and moves on.  If the click succeeds and
   the next step in the booking process appears (for example, the time
   selection panel), the script records the date as available.
6. If any target date is newly available, the script calls `send_email`
   to alert the user.
7. Waits for the specified interval and repeats the process.

Limitations
-----------
* The HTML structure of the ticket site may change over time.  You may
  need to adjust the XPaths or CSS selectors used in the script.
* Ticket availability is dynamic; the site may release tickets at a
  specific time of day.  Running the script continuously increases the
  chance of catching newly released tickets.
* For reliability, consider running the script on a server or cloud
  machine that can stay online until tickets are secured.
"""

import os
import time
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import logging

from selenium import webdriver
from selenium.common.exceptions import (NoSuchElementException,
                                        ElementClickInterceptedException,
                                        TimeoutException)
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import json
import requests


def load_env_file(path: Optional[Path] = None) -> None:
    """Load simple KEY=VALUE pairs from .env without overriding real env vars."""
    env_path = path or Path(__file__).with_name(".env")
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)


load_env_file()

# Configure logging for debug output.  The log level can be adjusted by setting
# the LOG_LEVEL environment variable (e.g., 'INFO' or 'DEBUG').
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# ---------------------------------------------------------------------------
# Configuration
#
# Define the dates to monitor.  Replace these with the actual dates you need.
TARGET_DATES: List[str] = [
    "2026-07-05",
    "2026-07-06",
    "2026-07-07",
    "2026-07-08",
    "2026-07-09",
]

# How often (in seconds) to check the ticket site.  Adjust this interval
# depending on how frequently you want to poll.  For example, 300 means
# every five minutes.
CHECK_INTERVAL: int = 10  # 5 minutes

# The URL of the Sagrada Família individual ticket product (subject to change).
PRODUCT_URL: str = (
    "https://tickets.sagradafamilia.org/en/1-individual/4375-sagrada-familia"
)

# ---------------------------------------------------------------------------
# Telegram Notification
#
# To receive notifications on Telegram, create a bot using BotFather and
# obtain the bot token.  Add the bot to a chat (e.g. your personal chat or
# a group) and obtain the chat ID.  Set the environment variables
# TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID accordingly.

def send_telegram_message(message: str) -> None:
    """Send a Telegram message using the Bot API.

    If the required environment variables are not set, falls back to
    printing the message to the console.

    Environment variables:
        TELEGRAM_BOT_TOKEN: Bot API token (e.g., '123456:ABC-DEF...')
        TELEGRAM_CHAT_ID:  Numeric chat ID or '@channelusername'
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print(f"[TELEGRAM ALERT] {message}")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        # Check response for errors
        if not response.ok:
            print(f"Telegram API error: {response.status_code} {response.text}")
            print(f"[TELEGRAM ALERT] {message}")
    except Exception as exc:
        print(f"Failed to send Telegram message: {exc}")
        print(f"[TELEGRAM ALERT] {message}")


def send_email(subject: str, body: str) -> None:
    """Send an e‑mail notification.

    This function is retained for backward compatibility but is no longer
    used by default.  For Telegram notifications, see
    :func:`send_telegram_message`.

    Environment variables required for e‑mail alerts:

    - EMAIL_HOST: SMTP host name, e.g. 'smtp.gmail.com'
    - EMAIL_PORT: SMTP port (an integer), e.g. '587'
    - EMAIL_USER: The username (e‑mail address) to send from.
    - EMAIL_PASSWORD: The SMTP password or app password.
    - EMAIL_TO: One or more recipient addresses separated by commas.

    If any of these variables are missing, this function will simply print
    the notification instead of sending an e‑mail.
    """
    host = os.environ.get("EMAIL_HOST")
    port = os.environ.get("EMAIL_PORT")
    username = os.environ.get("EMAIL_USER")
    password = os.environ.get("EMAIL_PASSWORD")
    recipients = os.environ.get("EMAIL_TO")

    # If any required variables are missing, fall back to console output.
    if not all([host, port, username, password, recipients]):
        print(f"[ALERT] {subject}\n{body}")
        return

    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = username
    msg["To"] = recipients

    try:
        with smtplib.SMTP(host, int(port)) as server:
            server.starttls()
            server.login(username, password)
            server.sendmail(username, recipients.split(","), msg.as_string())
    except Exception as exc:
        print(f"Failed to send e‑mail: {exc}")
        print(f"[ALERT] {subject}\n{body}")


def build_driver() -> webdriver.Chrome:
    """Configure and return a Chrome WebDriver.

    By default the driver runs in headless mode.  To disable headless mode and
    display a visible browser window (useful for debugging), set the
    environment variable HEADLESS to '0' or 'false'.
    """
    options = Options()
    # Determine whether to run headless based on environment variable
    headless_env = os.environ.get("HEADLESS", "1").lower()
    headless = not (headless_env in ["0", "false", "no"])
    if headless:
        options.add_argument("--headless=new")  # use new headless mode for Chrome ≥109
    else:
        logging.info("Running in visible (non-headless) mode for debugging.")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    logging.debug(f"Creating Chrome WebDriver with headless={headless}")
    driver = webdriver.Chrome(options=options)
    return driver


def accept_cookies(driver: webdriver.Chrome) -> None:
    """Accept or dismiss cookie banners if present.

    The Sagrada Família site shows a cookie banner with buttons such as
    "Accept all" and "Reject optional".  This function attempts to click
    whichever button is available to ensure the calendar loads.
    """
    try:
        # Wait for the cookie banner to appear (if any)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "div#onetrust-consent-sdk, div.cookie")
            )
        )
        logging.debug("Cookie banner detected.")
        # Attempt to click "Accept" or first button found
        for label in [
            "Accept all", "Accept", "Got it", "Reject optional", "Close"]:
            try:
                btn = driver.find_element(
                    By.XPATH, f"//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), '{label.lower()}')]"
                )
                btn.click()
                logging.info(f"Clicked cookie banner button labeled '{label}'.")
                break
            except NoSuchElementException:
                continue
    except TimeoutException:
        logging.debug("No cookie banner found.")
        pass  # no banner detected


def _find_first_element(driver, candidates):
    """Find the first visible element matching any of the candidate locators."""
    for by, selector in candidates:
        try:
            elements = driver.find_elements(by, selector)
            for element in elements:
                if element.is_displayed():
                    return element
        except Exception:
            continue
    return None


def _get_month_from_title(title_text: str) -> datetime | None:
    """Parse calendar month titles in common localizations."""
    normalized = re.sub(r"\s+", " ", title_text.strip().lower())
    if not normalized:
        return None

    # Supports formats like "07/2026", "July 2026", "julio 2026", etc.
    match = re.search(r"\b(0?\d|1[0-2])[\/.-](20\d{2})\b", normalized)
    if match:
        return datetime(int(match.group(2)), int(match.group(1)), 1)

    year_match = re.search(r"(20\d{2})", normalized)
    if not year_match:
        return None
    year = int(year_match.group(1))

    month_names = {
        "january": 1,
        "jan": 1,
        "february": 2,
        "feb": 2,
        "march": 3,
        "mar": 3,
        "april": 4,
        "apr": 4,
        "may": 5,
        "june": 6,
        "jun": 6,
        "july": 7,
        "jul": 7,
        "august": 8,
        "aug": 8,
        "september": 9,
        "sep": 9,
        "october": 10,
        "oct": 10,
        "november": 11,
        "nov": 11,
        "december": 12,
        "dec": 12,
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "setembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
        "gener": 1,
        "febrer": 2,
        "marc": 3,
        "abril": 4,
        "maig": 5,
        "juny": 6,
        "juliol": 7,
        "agost": 8,
        "setembre": 9,
        "octubre": 10,
        "novembre": 11,
        "desembre": 12,
    }

    tokens = normalized.replace(",", " ").split()
    month = None
    for token in tokens:
        if token in month_names:
            month = month_names[token]
            break
    if month is None:
        return None
    try:
        return datetime(year, month, 1)
    except ValueError:
        return None


def navigate_to_month(driver: webdriver.Chrome, target_date: datetime) -> None:
    """Navigate the calendar to the month of `target_date`.

    The calendar on the ticket page displays one month at a time with left and
    right arrows to move between months.  This function compares the current
    month shown with the target month and clicks the appropriate arrow to
    advance or rewind until the months match.
    """
    # Locate the month title and next/previous controls using several fallback
    # selectors because the site UI can vary by locale/update.
    title_selectors = [
        (By.CSS_SELECTOR, "div.calendar-header span.current-month"),
        (By.CSS_SELECTOR, "div.calendar-header .current-month"),
        (By.CSS_SELECTOR, "div.calendar-header__title"),
        (By.CSS_SELECTOR, "h3.current-month"),
        (By.CSS_SELECTOR, ".react-datepicker__current-month"),
        (By.CSS_SELECTOR, ".calendar-header h2"),
        (By.CSS_SELECTOR, "button[aria-label*='month'] span"),
    ]
    next_button_selectors = [
        (By.CSS_SELECTOR, "button.calendar-header__next"),
        (By.CSS_SELECTOR, "button[aria-label*='Next']"),
        (By.CSS_SELECTOR, "button[aria-label*='next']"),
        (By.CSS_SELECTOR, "button[title*='Next']"),
        (By.CSS_SELECTOR, "button[title*='next']"),
        (By.XPATH, "//button[contains(@class, 'next') or contains(@aria-label, 'next')]"),
    ]
    prev_button_selectors = [
        (By.CSS_SELECTOR, "button.calendar-header__previous"),
        (By.CSS_SELECTOR, "button[aria-label*='Previous']"),
        (By.CSS_SELECTOR, "button[aria-label*='previous']"),
        (By.CSS_SELECTOR, "button[title*='Previous']"),
        (By.CSS_SELECTOR, "button[title*='previous']"),
        (By.XPATH, "//button[contains(@class, 'prev') or contains(@aria-label, 'prev')]"),
    ]

    def current_title() -> str:
        title_element = _find_first_element(driver, title_selectors)
        if not title_element:
            return ""
        return title_element.text.strip()

    # Wait for any known calendar title element to appear.
    try:
        WebDriverWait(driver, 20).until(lambda d: current_title() != "")
    except TimeoutException:
        logging.warning("Unable to locate a calendar month header; skipping month navigation.")
        return
    logging.debug(f"Navigating to month {target_date.strftime('%B %Y')}.")

    current = _get_month_from_title(current_title())
    if current is None:
        logging.warning(
            f"Unable to parse initial month title '{current_title()}'; skipping month navigation."
        )
        return

    for _ in range(18):
        previous_title = current_title()
        current = _get_month_from_title(previous_title)
        if current is None:
            logging.warning(f"Unable to parse month title '{previous_title}'.")
            return

        # Compare year and month
        if current.year == target_date.year and current.month == target_date.month:
            logging.debug("Target month reached.")
            return

        if (current.year, current.month) < (target_date.year, target_date.month):
            logging.debug(
                f"Current month {current.strftime('%B %Y')} is before target; clicking next."
            )
            button = _find_first_element(driver, next_button_selectors)
            if not button:
                logging.warning("Could not find a Next month button; aborting month navigation.")
                return
            button.click()
        else:
            logging.debug(
                f"Current month {current.strftime('%B %Y')} is after target; clicking previous."
            )
            button = _find_first_element(driver, prev_button_selectors)
            if not button:
                logging.warning("Could not find a Previous month button; aborting month navigation.")
                return
            button.click()

        # Allow calendar to refresh and ensure header text changes.
        try:
            WebDriverWait(driver, 10).until(lambda d: current_title() != previous_title)
        except TimeoutException:
            logging.warning(
                f"Calendar header did not change after navigation from '{previous_title}'."
            )
            return

    logging.warning("Reached max month navigation steps without matching target month.")


def check_date_available(driver: webdriver.Chrome, date_str: str) -> bool:
    """Check whether tickets are available for a specific date.

    Args:
        driver: The Selenium WebDriver instance.
        date_str: Target date in ISO format (YYYY‑MM‑DD).

    Returns:
        True if the date is available (i.e. the date cell is clickable and
        leads to the next booking step), False otherwise.
    """
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    logging.info(f"Checking availability for {date_str}...")
    navigate_to_month(driver, target_date)

    # Build a selector for the day cell.  The official site uses buttons
    # with `data-day` or `aria-label` attributes containing the date.  We
    # attempt multiple strategies to locate the element.
    selectors = [
        # Strategy 1: button with exact ISO date in data-date attribute
        f"//button[@data-date='{date_str}']",
        # Strategy 2: button or div with aria-label containing the day number
        f"//button[contains(@aria-label, '{target_date.day}')]",
        f"//div[contains(@aria-label, '{target_date.day}')]",
    ]

    day_element = None
    for xpath in selectors:
        try:
            day_element = driver.find_element(By.XPATH, xpath)
            logging.debug(f"Found date element using selector: {xpath}")
            break
        except NoSuchElementException:
            continue

    if day_element is None:
        logging.debug(f"Date element for {date_str} not found.")
        return False

    # Try clicking the date.  If the element is disabled or hidden, an
    # exception will be raised.  After clicking, wait briefly to see if
    # a time selection panel or similar element appears, indicating
    # availability.
    try:
        day_element.click()
    except (ElementClickInterceptedException, Exception):
        logging.debug(f"Failed to click date {date_str}; likely sold out.")
        return False

    # Wait for a new section (e.g. time slot selector) to appear.  The time
    # selection container typically has a class like 'time-table' or
    # 'schedule', but this may change; we use a generic wait on a panel
    # containing times.
    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located(
                (By.XPATH, "//div[contains(@class,'time') or contains(@class,'schedule')]")
            )
        )
        logging.info(f"Tickets appear available for {date_str}.")
        return True
    except TimeoutException:
        logging.debug(f"No time slots found for {date_str}; still unavailable.")
        return False


def monitor_availability() -> None:
    """Main monitoring loop.

    Instantiates the WebDriver, loops through target dates and checks
    availability.  When a date becomes available, sends a notification.
    """
    notified_dates = set()  # keep track of dates already reported as available
    while True:
        logging.info("Starting a new availability check cycle.")
        driver = build_driver()
        try:
            driver.get(PRODUCT_URL)
            logging.debug(f"Navigated to {PRODUCT_URL}")

            # Accept cookie banner if present
            accept_cookies(driver)

            # Wait until the calendar loads; adjust selector if needed
            calendar_selectors = [
                (By.CSS_SELECTOR, "div.calendar"),
                (By.CSS_SELECTOR, "div#calendar"),
                (By.CSS_SELECTOR, ".calendar"),
                (By.CSS_SELECTOR, ".calendar-wrapper"),
            ]
            WebDriverWait(driver, 20).until(
                lambda d: _find_first_element(d, calendar_selectors) is not None
            )
            logging.debug("Calendar loaded.")

            for date_str in TARGET_DATES:
                if date_str in notified_dates:
                    # Skip dates already reported as available
                    continue
                is_available = check_date_available(driver, date_str)
                if is_available:
                    notified_dates.add(date_str)
                    message = (
                        f"사그라다 파밀리아 {date_str} 날짜의 티켓이 다시 판매 중입니다!\n"
                        f"지금 바로 예약 페이지로 이동하세요: {PRODUCT_URL}"
                    )
                    # Send Telegram notification.  Falls back to console output if
                    # TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.
                    logging.info(f"Sending notification for available date {date_str}.")
                    send_telegram_message(message)

        finally:
            driver.quit()

        # If all dates have been found available, exit the loop
        if len(notified_dates) == len(TARGET_DATES):
            print("All monitored dates are available. Exiting.")
            break

        # Sleep before the next check
        logging.info(f"Sleeping for {CHECK_INTERVAL} seconds before next cycle.")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    print(
        "Starting Sagrada Família ticket availability monitor.\n"
        f"Monitoring dates: {', '.join(TARGET_DATES)}\n"
        f"Checking every {CHECK_INTERVAL} seconds.\n"
        "Press Ctrl+C to stop."
    )
    try:
        monitor_availability()
    except KeyboardInterrupt:
        print("Monitoring stopped by user.")
