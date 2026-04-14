import time
import json
import os
import re
import sqlite3
import urllib.parse
import requests
import logging
import sys
import datetime

from bs4 import BeautifulSoup
from dotenv import load_dotenv
import configparser
import asyncio
from urllib.parse import urljoin

try:
    import psutil
except ModuleNotFoundError:
    psutil = None

try:
    from googletrans import Translator
except ModuleNotFoundError:
    class Translator:
        async def translate(self, title, src='ja', dest='en'):
            class _TranslationResult:
                def __init__(self, text):
                    self.text = text

            return _TranslationResult(title)

# --- Configuration Loading --- #
load_dotenv("key.env")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(__file__), 'config.ini')
if os.path.exists(config_path):
    config.read(config_path, encoding='utf-8')

# General Settings
db_file_name = config.get('BOT_SETTINGS', 'DB_FILE', fallback='seen_items.db') if config.has_section('BOT_SETTINGS') else 'seen_items.db'
DB_FILE = os.path.join(os.path.dirname(__file__), db_file_name)
# Legacy JSON path — used only for one-time migration on first run
SEEN_FILE = os.path.join(os.path.dirname(__file__), 'seen_items.json')

# Schedule Settings
# Delay Settings
KEYWORD_BATCH_DELAY = config.getint('DELAYS', 'KEYWORD_BATCH_DELAY', fallback=10) if config.has_section('DELAYS') else 10
FULL_CYCLE_DELAY = config.getint('DELAYS', 'FULL_CYCLE_DELAY', fallback=60) if config.has_section('DELAYS') else 60

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
translator = Translator()

# Create a single event loop for translations
translation_loop = None

# Cache for exchange rate to avoid repeated API calls
cached_exchange_rate = None
last_exchange_rate_update = None
EXCHANGE_RATE_CACHE_DURATION = 3600  # 1 hour in seconds

DEFAULT_BUYEE_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/136.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Upgrade-Insecure-Requests': '1',
    'Referer': 'https://buyee.jp/mercari/',
}

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

def send_telegram_photo(title: str, url: str, img_url: str, price: str, keyword_label: str = ""):
    """Sends a photo with a caption to the configured Telegram chat."""
    api_url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto'
    keyword_line = f"\nKeyword: {keyword_label}" if keyword_label else ""
    caption = f"<b>{title}</b>\nPrice: {price}{keyword_line}\n{url}"
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
def convert_price_to_yen(text: str, rate: float) -> tuple:
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
def create_buyee_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_BUYEE_HEADERS)
    return session


def fetch_with_retry(session, url, headers=None, max_retries=3, delay=2, timeout=30):
    last_error = None
    request_headers = headers or {}

    for attempt in range(max_retries):
        attempt_number = attempt + 1
        try:
            response = session.get(url, headers=request_headers, timeout=timeout)
            logging.info(
                f"Buyee request attempt {attempt_number}/{max_retries} -> "
                f"status={response.status_code} url={response.url}"
            )

            if response.status_code == 403:
                logging.error(
                    f"Buyee returned 403 for {response.url}. "
                    f"Likely anti-bot/session rejection. Cookies: {session.cookies.get_dict()}"
                )

            response.raise_for_status()
            return response
        except requests.RequestException as e:
            last_error = e
            if attempt == max_retries - 1:
                raise
            time.sleep(delay * (attempt + 1))

    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to fetch URL after retries: {url}")


def _extract_image_url(img_element) -> str:
    if not img_element:
        return ""

    image_url = ""
    if hasattr(img_element, 'get'):
        src = img_element.get('src', '')
        if isinstance(src, str):
            image_url = src

        data_bind = img_element.get('data-bind', '')
        if isinstance(data_bind, str):
            match = re.search(r"imagePath:\s*'([^']+)'", data_bind)
            if match:
                image_url = match.group(1)

    if image_url.startswith('//'):
        return f"https:{image_url}"
    if image_url and not image_url.startswith('http'):
        return urljoin('https://buyee.jp', image_url)
    return image_url


def _extract_items_from_search_html(soup: BeautifulSoup, keyword: str) -> list:
    item_elements = soup.select('ul.item-lists > li.list')
    if not item_elements:
        item_elements = soup.select('li.list')

    items = []
    for item_element in item_elements:
        try:
            link_element = item_element.find('a', href=re.compile(r'/mercari/item/'))
            if not link_element:
                continue

            item_url = link_element.get('href', '') if hasattr(link_element, 'get') else ''
            if not isinstance(item_url, str) or not item_url:
                continue
            if '/undefined/' in item_url:
                item_url = item_url.replace('/undefined/', '/')
            if not item_url.startswith('http'):
                item_url = urljoin('https://buyee.jp', item_url)

            title_element = item_element.find(['h2', 'span'], class_=re.compile(r'(^| )(name|simple_name__)'))
            # Keep raw Japanese title — translation happens later, only for new items
            title = title_element.get_text(strip=True) if title_element else "No title"

            price_element = item_element.find(['p', 'span'], class_=re.compile(r'(^| )(price|simple_price__)'))
            price_text = price_element.get_text(strip=True) if price_element else "No price"

            img_element = item_element.find('img')
            image_url = _extract_image_url(img_element)

            # Use Mercari item ID from URL as the stable unique identifier
            item_id = item_url.split('/')[-1].split('?')[0] if item_url else ""
            if not item_id:
                continue

            items.append({
                'id': item_id,
                'title': title,
                'price': price_text,
                'url': item_url,
                'image_url': image_url,
                'keyword': keyword
            })
        except Exception as e:
            logging.warning(f"Error parsing Buyee item element: {e}")
            continue

    return items


def fetch_items(keyword: str, conn: sqlite3.Connection, rate: float, session=None) -> list:
    """Fetches new items from Buyee for a given keyword using a shared session."""
    encoded_keyword = urllib.parse.quote(keyword)
    search_url = (
        f"https://buyee.jp/mercari/search?keyword={encoded_keyword}"
        f"&order-sort=desc-created_time&status=on_sale"
    )
    session = session or create_buyee_session()

    info_logger.info(f"🔍 Checking keyword: {keyword}")
    logging.info(f"Fetching Buyee search URL: {search_url}")

    try:
        response = fetch_with_retry(session, search_url)
    except requests.RequestException as e:
        logging.error(f"Failed to fetch Buyee search page for keyword '{keyword}': {e}")
        return []

    logging.info(f"Buyee session cookies after fetch: {session.cookies.get_dict()}")

    soup = BeautifulSoup(response.text, 'html.parser')
    items = _extract_items_from_search_html(soup, keyword)

    if not items:
        item_lists_count = len(soup.select('ul.item-lists'))
        mercari_link_count = len(soup.select('a[href*="/mercari/item/"]'))
        logging.warning(
            f"No item elements found for keyword '{keyword}'. "
            f"item-lists={item_lists_count}, mercari-links={mercari_link_count}"
        )
        return []

    info_logger.info(f"Found {len(items)} potential items for keyword: {keyword}")

    # Process items to check for new/cheaper items
    new_items = []
    for item in items:
        try:
            # Use Mercari item ID as the stable unique key
            item_id = item['id']

            # Convert price to numeric value for comparison
            formatted_price, numeric_price = convert_price_to_yen(item['price'], rate)
            logging.debug(f"Processing item: {item['title']} | Raw price: {item['price']} | Formatted: {formatted_price} | Numeric: {numeric_price}")

            if not formatted_price or not numeric_price:
                logging.debug(f"Skipping item due to price conversion issue: {item['title']} (Price text: {item['price']})")
                continue

            if not item['url'] or not item['image_url']:
                logging.debug(f"Skipping item due to missing URL or image: {item['title']}")
                continue

            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            row = get_seen_item(conn, item_id)

            if row is None:
                # New item — translate only now to avoid wasted API calls
                display_title = translate_title_with_fallback(item['title'])
                logging.debug(f"Item is new: {item['title']}")
                new_items.append({
                    'title': display_title, 'url': item['url'],
                    'image_url': item['image_url'], 'price': formatted_price,
                    'item_id': item_id, 'numeric_price': numeric_price,
                    'keyword': keyword, 'timestamp': timestamp,
                })
                upsert_seen_item(conn, item_id, numeric_price, timestamp)
                info_logger.info(f"New item found: {item['title']} at {formatted_price}")
            elif numeric_price < row[0]:
                display_title = translate_title_with_fallback(item['title'])
                logging.debug(f"Item is cheaper: {item['title']} | Old: {row[0]} | New: {numeric_price}")
                new_items.append({
                    'title': display_title, 'url': item['url'],
                    'image_url': item['image_url'], 'price': formatted_price,
                    'item_id': item_id, 'numeric_price': numeric_price,
                    'keyword': keyword, 'timestamp': timestamp,
                })
                upsert_seen_item(conn, item_id, numeric_price, timestamp)
                info_logger.info(f"Cheaper item found: {item['title']} at {formatted_price}")
            else:
                logging.debug(f"Item already seen: {item['title']} | Stored: {row[0]} | Current: {numeric_price}")

        except Exception as e:
            logging.warning(f"Error processing item: {e}")
            continue
    
    if not new_items:
        info_logger.info(f"✅ No new items found for keyword: {keyword}")
    else:
        info_logger.info(f"📦 Found {len(new_items)} new/cheaper items for keyword: {keyword}")
    
    return new_items

# --- Data Management --- #
def load_keywords() -> dict:
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

def _migrate_json_to_db(conn: sqlite3.Connection):
    """One-time import of seen_items.json into the DB, then renames the file."""
    if not os.path.exists(SEEN_FILE):
        return
    try:
        with open(SEEN_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        rows = [
            (item_id,
             info.get('price', 0),
             info.get('timestamp', now),
             info.get('timestamp', now))
            for item_id, info in data.items()
            if isinstance(info, dict)
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO seen_items (item_id, price, first_seen, last_seen) VALUES (?,?,?,?)",
            rows
        )
        conn.commit()
        os.rename(SEEN_FILE, SEEN_FILE + '.migrated')
        info_logger.info(f"Migrated {len(rows)} items from seen_items.json → seen_items.db")
    except Exception as e:
        logging.warning(f"JSON migration failed (non-fatal): {e}")


def _migrate_keywords_to_db(conn: sqlite3.Connection):
    """One-time import of keywords from config.ini [KEYWORDS] into the DB."""
    existing = conn.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
    if existing > 0:
        return  # Already populated — skip
    try:
        keywords_dict = dict(config.items('KEYWORDS'))
        rows = [(kw, label) for kw, label in keywords_dict.items()]
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO keywords (keyword, label) VALUES (?,?)", rows
            )
            conn.commit()
            info_logger.info(f"Migrated {len(rows)} keywords from config.ini to DB")
    except configparser.NoSectionError:
        pass


def init_db() -> sqlite3.Connection:
    """Open (or create) the SQLite DB, enable WAL mode, create tables, run migrations."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_items (
            item_id    TEXT    PRIMARY KEY,
            price      INTEGER NOT NULL,
            first_seen TEXT    NOT NULL,
            last_seen  TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS keywords (
            keyword TEXT PRIMARY KEY,
            label   TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id  TEXT    NOT NULL,
            keyword  TEXT    NOT NULL,
            price    INTEGER NOT NULL,
            sent_at  TEXT    NOT NULL
        )
    """)
    conn.commit()
    _migrate_json_to_db(conn)
    _migrate_keywords_to_db(conn)
    item_count = conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0]
    kw_count = conn.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
    info_logger.info(f"DB ready at {DB_FILE} ({item_count} items tracked, {kw_count} keywords)")
    return conn


def get_seen_item(conn: sqlite3.Connection, item_id: str):
    """Return (price, first_seen) tuple or None if not seen before."""
    return conn.execute(
        "SELECT price, first_seen FROM seen_items WHERE item_id = ?",
        (item_id,)
    ).fetchone()


def upsert_seen_item(conn: sqlite3.Connection, item_id: str, price: int, timestamp: str):
    """Insert new item or update price and last_seen for an existing item."""
    conn.execute("""
        INSERT INTO seen_items (item_id, price, first_seen, last_seen)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            price     = excluded.price,
            last_seen = excluded.last_seen
    """, (item_id, price, timestamp, timestamp))
    conn.commit()

# --- Keyword Management --- #
def load_keywords_from_db(conn: sqlite3.Connection) -> dict:
    """Returns {keyword: label} dict from the DB."""
    rows = conn.execute("SELECT keyword, label FROM keywords").fetchall()
    return {kw: label for kw, label in rows}


def _cmd_list_keywords(conn: sqlite3.Connection):
    kws = load_keywords_from_db(conn)
    if not kws:
        send_telegram_message("Nenhuma keyword cadastrada.\n\nUse /addkeyword &lt;keyword&gt; = &lt;label&gt;")
        return
    lines = ["📋 <b>Keywords ativas:</b>"]
    for kw, label in kws.items():
        lines.append(f"• {kw} = {label}")
    send_telegram_message("\n".join(lines))


def _cmd_add_keyword(conn: sqlite3.Connection, args: str):
    if "=" in args:
        parts = args.split("=", 1)
        keyword = parts[0].strip()
        label = parts[1].strip()
    else:
        keyword = args.strip()
        label = keyword
    if not keyword:
        send_telegram_message("❌ Uso: /addkeyword &lt;keyword&gt; = &lt;label&gt;")
        return
    conn.execute("INSERT OR REPLACE INTO keywords (keyword, label) VALUES (?,?)", (keyword, label))
    conn.commit()
    send_telegram_message(f"✅ Keyword adicionada: <b>{keyword}</b> = {label}")
    info_logger.info(f"Keyword added via Telegram: {keyword} = {label}")


def _cmd_remove_keyword(conn: sqlite3.Connection, keyword: str):
    if not keyword:
        send_telegram_message("❌ Uso: /removekeyword &lt;keyword&gt;")
        return
    cur = conn.execute("DELETE FROM keywords WHERE keyword = ?", (keyword,))
    conn.commit()
    if cur.rowcount:
        send_telegram_message(f"🗑 Keyword removida: <b>{keyword}</b>")
        info_logger.info(f"Keyword removed via Telegram: {keyword}")
    else:
        send_telegram_message(f"❌ Keyword não encontrada: <b>{keyword}</b>")


# --- Summary Command --- #
_PERIOD_LABELS = {
    '24h': ('últimas 24h', 1),
    '3d':  ('últimos 3 dias', 3),
    '7d':  ('últimos 7 dias', 7),
    '30d': ('últimos 30 dias', 30),
}
_DEFAULT_PERIOD = '24h'


def _cmd_summary(conn: sqlite3.Connection, args: str):
    """
    /summary               → all keywords, last 24h
    /summary 7d            → all keywords, last 7 days
    /summary IKKI          → keyword label "IKKI", last 24h
    /summary IKKI 7d       → keyword label "IKKI", last 7 days
    """
    parts = args.strip().split()
    period_key = _DEFAULT_PERIOD
    label_filter = None

    # Parse optional period token (last token if it matches a known period)
    if parts and parts[-1].lower() in _PERIOD_LABELS:
        period_key = parts[-1].lower()
        parts = parts[:-1]

    # Remaining tokens are a keyword label filter
    if parts:
        label_filter = " ".join(parts)

    period_label, days = _PERIOD_LABELS[period_key]
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

    if label_filter:
        # Resolve label → keyword
        row = conn.execute(
            "SELECT keyword FROM keywords WHERE label = ? COLLATE NOCASE", (label_filter,)
        ).fetchone()
        if not row:
            send_telegram_message(f"❌ Keyword com label <b>{label_filter}</b> não encontrada.\nUse /keywords para ver as ativas.")
            return
        kw_filter = row[0]
        rows = conn.execute(
            "SELECT keyword, COUNT(*) FROM notifications WHERE keyword = ? AND sent_at >= ? GROUP BY keyword",
            (kw_filter, since)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT keyword, COUNT(*) FROM notifications WHERE sent_at >= ? GROUP BY keyword",
            (since,)
        ).fetchall()

    title = f"📊 <b>Summary — {period_label}</b>"
    if label_filter:
        title += f" — {label_filter}"

    if not rows:
        send_telegram_message(f"{title}\n\nNenhum item encontrado nesse período.")
        return

    # Map keyword → label for display
    kw_labels = load_keywords_from_db(conn)
    lines = [title, ""]
    total = 0
    for kw, count in sorted(rows, key=lambda r: r[1], reverse=True):
        lbl = kw_labels.get(kw, kw)
        lines.append(f"• <b>{lbl}</b>: {count} item{'s' if count != 1 else ''}")
        total += count

    if not label_filter and len(rows) > 1:
        lines.append(f"\nTotal: {total} items")

    send_telegram_message("\n".join(lines))


# --- Telegram Command Polling --- #
def check_telegram_commands(conn: sqlite3.Connection, offset: int) -> int:
    """Poll getUpdates for new bot commands. Returns updated offset."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 0, "allowed_updates": ["message"]},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logging.warning(f"getUpdates failed: {e}")
        return offset

    for update in data.get("result", []):
        offset = update["update_id"] + 1
        msg = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "").strip()

        # Only process commands from the authorised chat
        if chat_id != str(CHAT_ID):
            logging.warning(f"Ignored command from unauthorised chat_id={chat_id}")
            continue

        if text == "/keywords" or text.startswith("/keywords "):
            _cmd_list_keywords(conn)
        elif text.startswith("/addkeyword "):
            _cmd_add_keyword(conn, text[len("/addkeyword "):].strip())
        elif text.startswith("/removekeyword "):
            _cmd_remove_keyword(conn, text[len("/removekeyword "):].strip())
        elif text == "/summary" or text.startswith("/summary "):
            _cmd_summary(conn, text[len("/summary"):].strip())

    return offset


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
    conn = None
    try:
        conn = init_db()
    except Exception as e:
        logging.critical(f"Failed to initialise DB: {e}")
        sys.exit(1)

    keywords_map = load_keywords_from_db(conn)
    if not keywords_map:
        send_telegram_message(
            "⚠️ Nenhuma keyword cadastrada.\n\n"
            "Use /addkeyword &lt;keyword&gt; = &lt;label&gt; para adicionar."
        )
        info_logger.info("No keywords in DB yet — bot will wait for /addkeyword commands.")

    info_logger.info(f"📋 {len(keywords_map)} keyword(s) loaded from DB")

    # Get exchange rate with fallback
    rate = get_exchange_rate_with_fallback()
    buyee_session = create_buyee_session()
    telegram_offset = 0

    try:
        while True:
            # Process any pending Telegram commands first
            telegram_offset = check_telegram_commands(conn, telegram_offset)

            # Reload keywords each cycle so additions/removals apply immediately
            keywords_map = load_keywords_from_db(conn)

            for kw_original, kw_translated in keywords_map.items():
                try:
                    info_logger.info(f"🔍 Starting search for keyword: {kw_original} (Translated: {kw_translated})")
                    items = fetch_items(kw_original, conn, rate, session=buyee_session)

                    if items:
                        info_logger.info(f"Sending {len(items)} items to Telegram for keyword: {kw_original}")
                        # Reverse so newest items appear first in Telegram
                        items.reverse()
                        for item in items:
                            send_telegram_photo(
                                item['title'], item['url'], item['image_url'],
                                item['price'], keyword_label=kw_translated
                            )
                            conn.execute(
                                "INSERT INTO notifications (item_id, keyword, price, sent_at) VALUES (?,?,?,?)",
                                (item['item_id'], kw_original, item['numeric_price'], item['timestamp'])
                            )
                        conn.commit()
                    else:
                        logging.info(f"No new items found for keyword: {kw_original}")

                except Exception as e:
                    logging.error(f"Error processing keyword '{kw_original}': {e}")
                    continue

                time.sleep(KEYWORD_BATCH_DELAY)

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
        if conn:
            conn.close()
        try:
            send_telegram_message("🔴 Mercari bot has stopped.")
        except:
            logging.error("Failed to send shutdown notification to Telegram")
        info_logger.info("🔴 Mercari bot is shutting down.")

def log_memory():
    if psutil is None:
        logging.info("Memory usage unavailable: psutil is not installed")
        return
    process = psutil.Process(os.getpid())
    logging.info(f"Memory usage: {process.memory_info().rss / 1024 ** 2:.2f} MB")

def check_telegram_connection():
    """Verify Telegram bot is working"""
    try:
        response = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False

def validate_config():
    """Validate all configuration settings"""
    if not BOT_TOKEN or not CHAT_ID:
        raise ValueError("Missing Telegram credentials in key.env")

    try:
        int(CHAT_ID)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CHAT_ID must be a valid integer, got: {CHAT_ID!r}") from exc

    if not os.path.exists(config_path):
        raise ValueError(f"Configuration file '{config_path}' not found")

    required_sections = ['BOT_SETTINGS', 'DELAYS']
    for section in required_sections:
        if not config.has_section(section):
            raise ValueError(f"Missing required section: {section}")

def test_url_accessibility(url: str, timeout: int = 5) -> bool:
    """Test if a URL is accessible by making a HEAD request."""
    try:
        response = requests.head(url, headers=DEFAULT_BUYEE_HEADERS, timeout=timeout)
        return response.status_code == 200
    except Exception as e:
        logging.debug(f"URL accessibility test failed for {url}: {e}")
        return False

if __name__ == "__main__":
    main()
