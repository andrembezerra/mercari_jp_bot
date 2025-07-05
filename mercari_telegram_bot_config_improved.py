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
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import configparser
import psutil
from googletrans import Translator
import asyncio

# Import urljoin from urllib.parse
from urllib.parse import urljoin

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
logging.basicConfig(
    level=logging.WARNING,  # Set to WARNING to reduce output
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Create a custom logger for important info messages
info_logger = logging.getLogger('info')
info_logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s'))
info_logger.addHandler(handler)
info_logger.propagate = False  # Prevent duplicate messages

# --- Global Variables --- #
daily_counts = defaultdict(int)
translator = Translator()

# Create a single event loop for translations
translation_loop = None

# Cache for exchange rate to avoid repeated API calls
cached_exchange_rate = None
last_exchange_rate_update = None
EXCHANGE_RATE_CACHE_DURATION = 3600  # 1 hour in seconds

def get_translation_loop():
    """Get or create the translation event loop."""
    global translation_loop
    if translation_loop is None or translation_loop.is_closed():
        translation_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(translation_loop)
    return translation_loop

def translate_title_with_fallback(title: str) -> str:
    """Translate title with graceful fallback to original if translation fails."""
    try:
        translated = get_translation_loop().run_until_complete(translator.translate(title, src='ja', dest='en'))
        if translated and hasattr(translated, 'text'):
            title_en = translated.text
            # Only use translation if it's different from original and not empty
            if title_en and title_en.strip() and title_en != title:
                return f"{title_en} ({title})"
            else:
                return title
        else:
            return title
    except Exception as e:
        logging.warning(f'Translation failed for title: {title[:50]}... | Error: {e}')
        return title  # Fallback to original title

def get_exchange_rate_with_fallback() -> float:
    """Get exchange rate with caching and fallback to default value."""
    global cached_exchange_rate, last_exchange_rate_update
    
    current_time = time.time()
    
    # Return cached rate if it's still valid
    if (cached_exchange_rate is not None and 
        last_exchange_rate_update is not None and 
        current_time - last_exchange_rate_update < EXCHANGE_RATE_CACHE_DURATION):
        logging.debug(f"Using cached exchange rate: {cached_exchange_rate}")
        return cached_exchange_rate
    
    try:
        rate = get_usd_to_jpy_rate()
        cached_exchange_rate = rate
        last_exchange_rate_update = current_time
        info_logger.info(f"✅ Updated exchange rate: {rate}")
        return rate
    except Exception as e:
        logging.warning(f"⚠️ Exchange rate fetch failed, using cached/default rate. Error: {e}")
        if cached_exchange_rate is not None:
            info_logger.info(f"Using cached exchange rate: {cached_exchange_rate}")
            return cached_exchange_rate
        else:
            info_logger.info("Using default exchange rate: 145.0")
            return 145.0

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
    # Handle Japanese yen format without ¥ symbol (e.g., "9,800 yen")
    yen_match = re.search(r'([\d,]+)\s*yen', text, re.IGNORECASE)
    if yen_match:
        amount_str = yen_match.group(1)
        try:
            amount_int = int(amount_str.replace(',', ''))
            return f"¥{amount_int:,}".replace(",", "."), amount_int
        except ValueError:
            logging.warning(f"Could not parse yen amount '{amount_str}' from text: {text}")
            return None, None
    
    # Handle other currency formats with symbols
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

# --- Buyee Scraping --- #
def fetch_with_retry(url, headers, max_retries=3, delay=2):
    for attempt in range(max_retries):
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            if attempt == attempt == max_retries - 1:
                raise e
            time.sleep(delay * (attempt + 1))  # Exponential backoff

def fetch_items(keyword: str, seen_items: dict, rate: float) -> list[tuple]:
    """Fetches new items from Buyee for a given keyword using requests and BeautifulSoup."""
    encoded_keyword = urllib.parse.quote(keyword)
    
    info_logger.info(f"🔍 Checking keyword: {keyword}")
    
    # First, fetch the parent page to get the iframe src
    parent_url = f"https://buyee.jp/mercari/search?keyword={encoded_keyword}&order-sort=desc-created_time&status=on_sale"
    logging.info(f"Fetching parent URL: {parent_url}")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        parent_response = fetch_with_retry(parent_url, headers)
    except requests.RequestException as e:
        logging.error(f"Failed to fetch parent page for keyword '{keyword}': {e}")
        return []
    
    if parent_response is None:
        logging.error(f"Parent response is None for keyword '{keyword}'")
        return []
    
    parent_soup = BeautifulSoup(parent_response.text, 'html.parser')
    
    # Log all iframes found for debugging
    all_iframes = parent_soup.find_all('iframe')
    logging.info(f"Found {len(all_iframes)} iframe(s) on the page")
    for i, iframe in enumerate(all_iframes):
        iframe_id = iframe.get('id') or 'NO_ID'  # type: ignore[attr-defined]
        iframe_src = iframe.get('src') or 'NO_SRC'  # type: ignore[attr-defined]
        logging.info(f"Iframe {i+1}: id='{iframe_id}', src='{iframe_src}'")
    
    iframe_tag = parent_soup.find('iframe', id='search_result_iframe')
    
    if not iframe_tag:
        logging.error("Could not find search_result_iframe.")
        return []
    
    # Extract iframe URL from JavaScript code since iframe has no src initially
    scripts = parent_soup.find_all('script')
    script_pattern = r"document\.querySelector\('#search_result_iframe'\)\.src\s*=\s*'([^']+)'"
    iframe_url = None
    
    logging.info(f"Searching through {len(scripts)} scripts for iframe URL pattern")
    
    for i, script in enumerate(scripts):
        # Use getattr to avoid linter errors for .string
        script_content = getattr(script, 'string', None)
        if script_content:
            match = re.search(script_pattern, script_content)
            if match:
                iframe_url = match.group(1)
                logging.info(f"Found iframe URL in script {i+1}: {iframe_url}")
                break
            else:
                # Log a sample of script content for debugging
                if i < 3:  # Only log first 3 scripts
                    sample_content = script_content[:200] if len(script_content) > 200 else script_content
                    logging.debug(f"Script {i+1} sample content: {sample_content}")
    
    if not iframe_url:
        logging.error("Could not extract iframe URL from JavaScript code.")
        # Try alternative patterns
        alternative_patterns = [
            r"iframe.*src\s*=\s*'([^']+)'",
            r"search_result_iframe.*src\s*=\s*'([^']+)'",
            r"src\s*=\s*'([^']*mercari[^']*)'"
        ]
        
        for pattern in alternative_patterns:
            for script in scripts:
                script_content = getattr(script, 'string', None)
                if script_content:
                    match = re.search(pattern, script_content, re.IGNORECASE)
                    if match:
                        iframe_url = match.group(1)
                        logging.info(f"Found iframe URL using alternative pattern '{pattern}': {iframe_url}")
                        break
            if iframe_url:
                break
    
    if not iframe_url:
        logging.error("Could not extract iframe URL from JavaScript code.")
        return []
    
    # Make iframe URL absolute if it's relative
    if not iframe_url.startswith('http'):
        if iframe_url.startswith('/'):
            iframe_url = urljoin('https://buyee.jp', iframe_url)
        else:
            iframe_url = urljoin('https://buyee.jp', iframe_url)
        logging.info(f"Converted iframe URL to absolute: {iframe_url}")
    
    logging.info(f"Final iframe URL: {iframe_url}")
    
    # Test if the iframe URL is accessible
    try:
        test_response = requests.head(iframe_url, headers=headers, timeout=5)
        logging.info(f"Iframe URL accessibility test: {test_response.status_code}")
    except Exception as e:
        logging.warning(f"Iframe URL accessibility test failed: {e}")
    
    # Log the iframe URL structure for debugging
    logging.info(f"Iframe URL structure - Protocol: {iframe_url.split('://')[0] if '://' in iframe_url else 'None'}")
    logging.info(f"Iframe URL structure - Domain: {iframe_url.split('/')[2] if len(iframe_url.split('/')) > 2 else 'None'}")
    
    # Fetch the iframe content
    try:
        response = fetch_with_retry(iframe_url, headers)
    except requests.RequestException as e:
        logging.error(f"Failed to fetch iframe content for keyword '{keyword}': {e}")
        return []
    
    if response is None:
        logging.error(f"Iframe response is None for keyword '{keyword}'")
        return []
    
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Find all item elements in the iframe
    # Buyee uses div.simple_item__Ewdl1 for items
    item_elements = soup.find_all('div', class_='simple_item__Ewdl1')
    
    if not item_elements:
        logging.warning(f"No item elements found for keyword: {keyword}")
        return []
    
    info_logger.info(f"Found {len(item_elements)} potential items for keyword: {keyword}")
    
    items = []
    for item_element in item_elements:
        try:
            # Find the link element
            link_element = item_element.find('a', class_='simple_container__llX1q')  # type: ignore[attr-defined]
            if not link_element:
                continue
                
            # Extract item URL
            item_url = link_element.get('href', '') if hasattr(link_element, 'get') else ''  # type: ignore[attr-defined]
            if not isinstance(item_url, str):
                item_url = str(item_url)
            if not item_url:
                continue
            # Make URL absolute if it's relative
            original_item_url = item_url
            if not item_url.startswith('http'):
                # Fix the /undefined/ issue in the URL path
                if '/undefined/' in item_url:
                    item_url = item_url.replace('/undefined/', '/')
                item_url = urljoin('https://buyee.jp', item_url)
            
            # Extract item title
            title_element = link_element.find('span', class_='simple_name__XMcbt')  # type: ignore[attr-defined]
            title = title_element.get_text(strip=True) if title_element else "No title"
            # Translate title from Japanese to English with graceful fallback
            full_title = translate_title_with_fallback(title)
            
            # Extract price
            price_element = link_element.find('span', class_='simple_price__h13DP')  # type: ignore[attr-defined]
            price_text = price_element.get_text(strip=True) if price_element else "No price"
            
            # Extract image URL
            img_element = link_element.find('img', class_='cdn_container__T7Lek')  # type: ignore[attr-defined]
            image_url = img_element.get('src', '') if img_element and hasattr(img_element, 'get') else ""  # type: ignore[attr-defined]
            if not isinstance(image_url, str):
                image_url = str(image_url)
            
            # Make image URL absolute if it's relative
            original_image_url = image_url
            if not image_url.startswith('http'):
                image_url = urljoin('https://buyee.jp', image_url)
            
            # Extract item ID from URL
            item_id = item_url.split('/')[-1].split('?')[0] if item_url else ""
            
            items.append({
                'id': item_id,
                'title': full_title,
                'price': price_text,
                'url': item_url,
                'image_url': image_url,
                'keyword': keyword
            })
            
            # Debug logging for first few items
            if len(items) <= 3:
                logging.info(f"Item {len(items)}: URL={item_url}, Image={image_url}")
                logging.info(f"Original item URL was: {original_item_url}")
                # Test URL accessibility for debugging
                if test_url_accessibility(item_url):
                    logging.info(f"✅ Item URL {len(items)} is accessible")
                else:
                    logging.warning(f"❌ Item URL {len(items)} is NOT accessible")
                    # Try to get more info about the failed request
                    try:
                        headers = {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                        }
                        response = requests.head(item_url, headers=headers, timeout=5)
                        logging.warning(f"URL {item_url} returned status code: {response.status_code}")
                    except Exception as e:
                        logging.warning(f"URL {item_url} failed with error: {e}")
            
        except Exception as e:
            logging.warning(f"Error parsing item element: {e}")
            continue

    # Process items to check for new/cheaper items
    new_items = []
    for item in items:
        try:
            # Convert price to numeric value for comparison
            formatted_price, numeric_price = convert_price_to_yen(item['price'], rate)
            logging.debug(f"Processing item: {item['title']} | Raw price: {item['price']} | Formatted: {formatted_price} | Numeric: {numeric_price}")
            
            if not formatted_price or not numeric_price:
                logging.debug(f"Skipping item due to price conversion issue: {item['title']} (Price text: {item['price']})")
                continue
                
            if not item['url'] or not item['image_url']:
                logging.debug(f"Skipping item due to missing URL or image: {item['title']}")
                continue
                
            # Create unique signature for item
            item_signature = hashlib.md5((item['title'].lower() + item['image_url']).encode()).hexdigest()
            logging.debug(f"Item signature: {item_signature}")
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # Check if item is new or if a lower price is found
            if item_signature not in seen_items:
                logging.debug(f"Item is new: {item['title']}")
                new_items.append((item['title'], item['url'], item['image_url'], formatted_price, timestamp))
                seen_items[item_signature] = {
                    'price': numeric_price,
                    'timestamp': timestamp
                }
                info_logger.info(f"New item found: {item['title']} at {formatted_price}")
            elif numeric_price < seen_items[item_signature]['price']:
                logging.debug(f"Item is cheaper than before: {item['title']} | Old price: {seen_items[item_signature]['price']} | New price: {numeric_price}")
                new_items.append((item['title'], item['url'], item['image_url'], formatted_price, timestamp))
                seen_items[item_signature]['price'] = numeric_price
                seen_items[item_signature]['timestamp'] = timestamp
                info_logger.info(f"Cheaper item found: {item['title']} at {formatted_price}")
            else:
                logging.debug(f"Item already seen or not cheaper: {item['title']} | Old price: {seen_items[item_signature]['price']} | New price: {numeric_price}")
                
        except Exception as e:
            logging.warning(f"Error processing item: {e}")
            continue
    
    if not new_items:
        info_logger.info(f"✅ No new items found for keyword: {keyword}")
    else:
        info_logger.info(f"📦 Found {len(new_items)} new/cheaper items for keyword: {keyword}")
    
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
    # Validate configuration first
    try:
        validate_config()
        info_logger.info("✅ Configuration validation passed")
    except ValueError as e:
        logging.critical(f"Configuration error: {e}")
        sys.exit(1)
    
    # Test Telegram connection
    if not check_telegram_connection():
        logging.critical("❌ Cannot connect to Telegram API. Please check your BOT_TOKEN.")
        sys.exit(1)
    else:
        info_logger.info("✅ Telegram connection verified")
    
    info_logger.info("🚀 Mercari bot is starting...")
    
    # Load data with error handling
    try:
        seen_items = load_seen_items()
        keywords_map = load_keywords()
        original_keywords = list(keywords_map.keys())
    except Exception as e:
        logging.critical(f"Failed to load data: {e}")
        sys.exit(1)
    
    if not original_keywords:
        logging.critical("No keywords loaded. Please add keywords to the [KEYWORDS] section in config.ini.")
        sys.exit(1)
    
    info_logger.info(f"📋 Loaded {len(original_keywords)} keywords")
    
    # Get exchange rate with fallback
    rate = get_exchange_rate_with_fallback()

    schedule.every().day.at(DAILY_SUMMARY_TIME).do(send_daily_summary)

    try:
        while True:
            for kw_original in original_keywords:
                try:
                    kw_translated = keywords_map.get(kw_original, kw_original)
                    info_logger.info(f"🔍 Starting search for keyword: {kw_original} (Translated: {kw_translated})")
                    items = fetch_items(kw_original, seen_items, rate)

                    if items:
                        send_telegram_message(f"🔍 Found new listings for: <b>{kw_translated}</b>...")
                        daily_counts[kw_original] += len(items)
                        logging.info(f"🚀 Sending {len(items)} items for keyword: {kw_original}")
                        info_logger.info(f"Sending {len(items)} items to Telegram for keyword: {kw_original}")
                        # Reverse the items list so newest items appear first in Telegram
                        items.reverse()
                        for item in items:
                            # item is a tuple: (title, url, image_url, formatted_price, timestamp)
                            title, url, image_url, formatted_price, timestamp = item
                            send_telegram_photo(title, url, image_url, formatted_price, timestamp)
                        send_telegram_message(f"✅ Done! Found <b>{len(items)}</b> new item{'s' if len(items) != 1 else ''} for <b>{kw_translated}</b>.")
                    else:
                        logging.info(f"No new items found for keyword: {kw_original}")
                        
                except Exception as e:
                    logging.error(f"Error processing keyword '{kw_original}': {e}")
                    # Continue with next keyword instead of crashing
                    continue

                time.sleep(KEYWORD_BATCH_DELAY)

            # Save data periodically
            try:
                save_seen_items(seen_items)
            except Exception as e:
                logging.error(f"Failed to save seen items: {e}")
            
            schedule.run_pending()
            info_logger.info("✅ Finished a full cycle of keyword searches. Waiting for next cycle...")
            time.sleep(FULL_CYCLE_DELAY)

    except KeyboardInterrupt:
        info_logger.info("🛑 Bot stopped by user (KeyboardInterrupt).")
    except Exception as e:
        logging.critical(f"An unhandled critical error occurred: {e}", exc_info=True)
        try:
            send_telegram_message(f"❗️ An error occurred: {e}")
        except:
            logging.error("Failed to send error notification to Telegram")
        logging.error("Shutting down due to critical error.")
    finally:
        try:
            send_telegram_message("🔴 Mercari bot has stopped.")
        except:
            logging.error("Failed to send shutdown notification to Telegram")
        info_logger.info("🔴 Mercari bot is shutting down.")

def log_memory():
    process = psutil.Process(os.getpid())
    logging.info(f"Memory usage: {process.memory_info().rss / 1024 ** 2:.2f} MB")

def check_telegram_connection():
    """Verify Telegram bot is working"""
    try:
        response = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe")
        return response.status_code == 200
    except:
        return False

def validate_config():
    """Validate all configuration settings"""
    required_sections = ['BOT_SETTINGS', 'SCHEDULE', 'DELAYS', 'KEYWORDS']
    for section in required_sections:
        if not config.has_section(section):
            raise ValueError(f"Missing required section: {section}")

def test_url_accessibility(url: str, timeout: int = 5) -> bool:
    """Test if a URL is accessible by making a HEAD request."""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.head(url, headers=headers, timeout=timeout)
        return response.status_code == 200
    except Exception as e:
        logging.debug(f"URL accessibility test failed for {url}: {e}")
        return False

if __name__ == "__main__":
    main()

