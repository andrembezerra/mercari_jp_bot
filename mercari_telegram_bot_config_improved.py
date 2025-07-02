import time
import json
import os
import re
import urllib.parse
import requests
import logging
import sys
import datetime
import schedule
import hashlib
from collections import defaultdict
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from dotenv import load_dotenv
import configparser

# --- Configuration Loading --- #
load_dotenv("key.env")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    logging.critical("Missing Telegram credentials in .env file!")
    sys.exit(1)

config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), 'config.ini')
if not os.path.exists(config_path):
    logging.critical(f"Configuration file '{config_path}' not found. Please create it.")
    sys.exit(1)
# Exxpecting UTF-8 encoding when reading the config file
config.read(config_path, encoding='utf-8')

# General Settings
MAX_SEEN_ITEMS = config.getint('BOT_SETTINGS', 'MAX_SEEN_ITEMS', fallback=6000)
SEEN_FILE = os.path.join(os.path.dirname(__file__), config.get('BOT_SETTINGS', 'SEEN_FILE', fallback='seen_items.json'))

# Schedule Settings
DAILY_SUMMARY_TIME = config.get('SCHEDULE', 'DAILY_SUMMARY_TIME', fallback='12:30')

# Delay Settings
KEYWORD_BATCH_DELAY = config.getint('DELAYS', 'KEYWORD_BATCH_DELAY', fallback=10)
FULL_CYCLE_DELAY = config.getint('DELAYS', 'FULL_CYCLE_DELAY', fallback=60)

# --- Logging Setup --- #
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# --- Global Variables --- #
daily_counts = defaultdict(int)

# --- Telegram Functions --- #
def send_telegram_message(text: str):
    """Sends a text message to the configured Telegram chat."""
    api_url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    payload = {
        'chat_id': CHAT_ID,
        'text': text,
        'parse_mode': 'HTML'
    }
    try:
        response = requests.post(api_url, data=payload, timeout=5)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        logging.info(f"📝 Sent message: {text[:50]}...")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send message: {e}")

def send_telegram_photo(title: str, url: str, img_url: str, price: str, timestamp: str):
    """Sends a photo with a caption to the configured Telegram chat."""
    api_url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto'
    caption = f"<b>{title}</b>\nPrice: {price}\nTime: {timestamp}\n{url}"
    payload = {
        'chat_id': CHAT_ID,
        'photo': img_url,
        'caption': caption,
        'parse_mode': 'HTML'
    }
    try:
        response = requests.post(api_url, data=payload, timeout=10)
        response.raise_for_status()
        logging.info(f"Sent photo for: {title}")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send photo for {title}: {e}")

# --- Price Conversion --- #
def convert_price_to_yen(text: str, rate: float) -> tuple[str | None, int | None]:
    """Converts a price string to Japanese Yen based on the provided exchange rate."""
    match = re.search(r'(¥|US\$|\$)\s*([\d,]+)', text)
    if not match:
        logging.debug(f"No price found in text: {text}")
        return None, None

    symbol, amount_str = match.groups()
    try:
        amount_int = int(amount_str.replace(',', ''))
    except ValueError:
        logging.warning(f"Could not parse amount '{amount_str}' from text: {text}")
        return None, None

    yen = None
    if symbol in ['US$', '$']:
        yen = int(amount_int * rate)
    elif symbol == '¥':
        yen = amount_int
    else:
        logging.warning(f"Unknown currency symbol '{symbol}' in text: {text}")
        return None, None

    return f"¥{yen:,}".replace(",", "."), yen

# --- Mercari Scraping --- #
def initialize_webdriver():
    """Initializes and returns a Chrome WebDriver instance."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,8192")
    options.add_argument("--blink-settings=imagesEnabled=false")

    for attempt in range(3):
        try:
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            logging.info("WebDriver initialized successfully.")
            return driver
        except Exception as e:
            logging.error(f"WebDriver initialization failed (attempt {attempt + 1}/3): {e}")
            time.sleep(5)
    logging.critical("❌ WebDriver could not initialize after multiple attempts.")
    return None

             # Retry logic for page loading - Pedding
                retries = 3
                for attempt in range(retries):
                    try:
                        driver.get(search_url)
                        break
                    except Exception as e:
                        logging.warning(f"[{keyword}] Attempt {attempt+1} failed to load page: {e}")
                        time.sleep(5)
                else:
                    logging.critical(f"[{keyword}] Failed to load page after multiple attempts.")
                    driver.quit()
                    return []

def fetch_items(keyword: str, seen_items: dict, rate: float, driver) -> list[tuple]:
    """Fetches new items from Mercari for a given keyword using the provided WebDriver."""
    if not driver:
        logging.error("WebDriver is not available. Cannot fetch items.")
        return []

    encoded_keyword = urllib.parse.quote(keyword)
    url = f"https://jp.mercari.com/zh-TW/search?keyword={encoded_keyword}&lang=zh-TW&sort=created_time&order=desc&status=on_sale"

    logging.info(f"Navigating to: {url}")
    driver.get(url)

    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'li[data-testid="item-cell"]'))
        )
        logging.info("Page loaded and items found.")
    except Exception as e:
        logging.error(f"Timeout waiting for items for keyword '{keyword}': {e}")
        return []

    # Scroll down to load more items
    for _ in range(2):  # Reduced scroll depth
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(1)

    elements = driver.find_elements(By.CSS_SELECTOR, 'li[data-testid="item-cell"]')
    logging.info(f"Found {len(elements)} potential items for keyword: {keyword}")
    elements.reverse()  # Process older items first

    new_items = []
    for el in elements:
        try:
            href = el.find_element(By.XPATH, ".//a").get_attribute('href')
            text = el.text.strip()
            title = text.split('\n')[0] if text else "Untitled Item"
            img = el.find_element(By.CSS_SELECTOR, "img").get_attribute('src')
            formatted_price, numeric_price = convert_price_to_yen(text, rate)

            if not formatted_price or not numeric_price:
                logging.debug(f"Skipping item due to price conversion issue: {title}")
                continue

            item_signature = hashlib.md5((title.lower() + img).encode()).hexdigest()
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Check if item is new or if a lower price is found
            if item_signature not in seen_items or numeric_price < seen_items[item_signature]['price']:
                new_items.append((title, href, img, formatted_price, timestamp))
                seen_items[item_signature] = {
                    'price': numeric_price,
                    'timestamp': timestamp
                }
                logging.info(f"New or cheaper item found: {title} at {formatted_price}")
            else:
                logging.debug(f"Item already seen or not cheaper: {title}")

        except Exception as e:
            logging.warning(f"Error parsing item element: {e} - Element text: {el.text[:100]}...")

    return new_items

# --- Data Management --- #
def load_keywords() -> dict[str, str]:
    """Loads keywords and their English translations from the config file."""
    try:
        keywords_dict = dict(config.items('KEYWORDS'))
        if not keywords_dict:
            logging.critical("No keywords found in the [KEYWORDS] section of config.ini.")
            return {}
        logging.info(f"Loaded {len(keywords_dict)} keywords from config.ini.")
        return keywords_dict
    except configparser.NoSectionError:
        logging.critical("No [KEYWORDS] section found in config.ini.")
        return {}

def load_seen_items() -> dict:
    """Loads seen items from the JSON file."""
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, 'r', encoding='utf-8') as f:
                seen_data = json.load(f)
            logging.info(f"Loaded {len(seen_data)} seen items.")
            return seen_data
        except json.JSONDecodeError as e:
            logging.error(f"Error decoding JSON from '{SEEN_FILE}': {e}. Starting with empty seen items.")
            return {}
        except Exception as e:
            logging.error(f"Error loading seen items from '{SEEN_FILE}': {e}. Starting with empty seen items.")
            return {}
    logging.info("No seen items file found. Starting fresh.")
    return {}

def save_seen_items(seen_items: dict):
    """Saves seen items to the JSON file, trimming to MAX_SEEN_ITEMS."""
    # Keep only the most recent MAX_SEEN_ITEMS
    trimmed_items = dict(list(seen_items.items())[-MAX_SEEN_ITEMS:])
    try:
        with open(SEEN_FILE, 'w', encoding='utf-8') as f:
            json.dump(trimmed_items, f, ensure_ascii=False, indent=2)
        logging.info(f"Saved {len(trimmed_items)} seen items to '{SEEN_FILE}'.")
    except Exception as e:
        logging.error(f"Failed to save seen items: {e}")

# --- Exchange Rate --- #
def get_usd_to_jpy_rate() -> float:
    """Fetches the current USD to JPY exchange rate from an API."""
    try:
        response = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
        response.raise_for_status()
        data = response.json()
        rate = float(data["rates"]["JPY"])
        logging.info(f"Fetched USD to JPY exchange rate: {rate}")
        return rate
    except requests.exceptions.RequestException as e:
        logging.warning(f"⚠️ Failed to fetch exchange rate, using fallback (145.0). Error: {e}")
        return 145.0
    except (KeyError, TypeError) as e:
        logging.warning(f"⚠️ Failed to parse exchange rate data, using fallback (145.0). Error: {e}")
        return 145.0

# --- Daily Summary --- #
def send_daily_summary():
    """Sends a daily summary of new items found to Telegram."""
    date = datetime.date.today().isoformat()
    lines = [f"📊 Mercari Summary — {date}\n"]
    if not daily_counts:
        lines.append("No activity recorded today.")
    else:
        for kw_original, count in daily_counts.items():
            # Assuming keywords_map is accessible or passed, or we re-load it
            # For simplicity, let's re-load it here, or pass it as an argument
            keywords_map = load_keywords() # Re-load for summary, or pass as arg
            kw_translated = keywords_map.get(kw_original, kw_original) # Use original if translation not found
            lines.append(f"• {kw_translated}: {count} new item{'s' if count != 1 else ''}")
    send_telegram_message("\n".join(lines))
    daily_counts.clear()
    logging.info("Daily summary sent and daily counts cleared.")

# --- Main Logic --- #
def main():
    logging.info("✅ Mercari bot is starting...")
    seen_items = load_seen_items()
    keywords_map = load_keywords() # Load keywords as a dictionary (original -> translated)
    original_keywords = list(keywords_map.keys()) # Get original keywords for iteration
    rate = get_usd_to_jpy_rate()

    if not original_keywords:
        logging.critical("No keywords loaded. Please add keywords to the [KEYWORDS] section in config.ini.")
        sys.exit(1)

    driver = initialize_webdriver()
    if not driver:
        sys.exit(1) # Exit if WebDriver fails to initialize

    schedule.every().day.at(DAILY_SUMMARY_TIME).do(send_daily_summary)

    try:
        while True:
            for kw_original in original_keywords:
                kw_translated = keywords_map.get(kw_original, kw_original) # Get translated keyword
                logging.info(f"Starting search for keyword: {kw_original} (Translated: {kw_translated})")
                items = fetch_items(kw_original, seen_items, rate, driver)

                if items:
                    send_telegram_message(f"🔍 Found new listings for: <b>{kw_translated}</b>...")
                    daily_counts[kw_original] += len(items) # Use original keyword for daily_counts
                    logging.info(f"🚀 Sending {len(items)} items for keyword: {kw_original}")
                    # Sort by timestamp to send in chronological order if desired, or by price etc.
                    for title, href, img, price, ts in sorted(items, key=lambda x: x[4]):
                        send_telegram_photo(title, href, img, price, ts)
                    send_telegram_message(f"✅ Done! Found <b>{len(items)}</b> new item{'s' if len(items) != 1 else ''} for <b>{kw_translated}</b>.")
                else:
                    logging.info(f"No new items found for keyword: {kw_original}")

                time.sleep(KEYWORD_BATCH_DELAY)  # Delay between keyword batches to avoid overwhelming Mercari

            save_seen_items(seen_items)
            schedule.run_pending()
            logging.info("Finished a full cycle of keyword searches. Waiting for next cycle...")
            time.sleep(FULL_CYCLE_DELAY) # Wait before starting the next full cycle

    except KeyboardInterrupt:
        logging.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logging.critical(f"An unhandled critical error occurred: {e}", exc_info=True)
        send_telegram_message(f"❗️ An error occurred: {e}")
        logging.error("Shutting down due to critical error.")
    finally:
        if driver:
            driver.quit()
            logging.info("WebDriver closed.")
        logging.info("Mercari bot is shutting down.")

if __name__ == "__main__":
    main()


