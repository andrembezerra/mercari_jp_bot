import datetime
import logging
import re
import sqlite3
import time
import urllib.parse
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.database import get_seen_item, upsert_seen_item
from src.logging_setup import info_logger
from src.pricing import convert_price_to_yen
from src.translation import TranslationService
from src.types import ItemData, NotificationItem

DEFAULT_BUYEE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://buyee.jp/mercari/",
}


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
        except requests.RequestException as exc:
            last_error = exc
            if attempt == max_retries - 1:
                raise
            time.sleep(delay * (attempt + 1))

    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to fetch URL after retries: {url}")


def extract_image_url(img_element) -> str:
    if not img_element:
        return ""

    image_url = ""
    if hasattr(img_element, "get"):
        src = img_element.get("src", "")
        if isinstance(src, str):
            image_url = src

        data_bind = img_element.get("data-bind", "")
        if isinstance(data_bind, str):
            match = re.search(r"imagePath:\s*'([^']+)'", data_bind)
            if match:
                image_url = match.group(1)

    if image_url.startswith("//"):
        return f"https:{image_url}"
    if image_url and not image_url.startswith("http"):
        return urljoin("https://buyee.jp", image_url)
    return image_url


def extract_items_from_search_html(soup: BeautifulSoup, keyword: str) -> list[ItemData]:
    item_elements = soup.select("ul.item-lists > li.list")
    if not item_elements:
        item_elements = soup.select("li.list")

    items: list[ItemData] = []
    for item_element in item_elements:
        try:
            link_element = item_element.find("a", href=re.compile(r"/mercari/item/"))
            if not link_element:
                continue

            item_url = link_element.get("href", "") if hasattr(link_element, "get") else ""
            if not isinstance(item_url, str) or not item_url:
                continue
            if "/undefined/" in item_url:
                item_url = item_url.replace("/undefined/", "/")
            if not item_url.startswith("http"):
                item_url = urljoin("https://buyee.jp", item_url)

            title_element = item_element.find(["h2", "span"], class_=re.compile(r"(^| )(name|simple_name__)"))
            title = title_element.get_text(strip=True) if title_element else "No title"

            price_element = item_element.find(["p", "span"], class_=re.compile(r"(^| )(price|simple_price__)"))
            price_text = price_element.get_text(strip=True) if price_element else "No price"

            img_element = item_element.find("img")
            image_url = extract_image_url(img_element)

            item_id = item_url.split("/")[-1].split("?")[0] if item_url else ""
            if not item_id:
                continue

            items.append(
                {
                    "id": item_id,
                    "title": title,
                    "price": price_text,
                    "url": item_url,
                    "image_url": image_url,
                    "keyword": keyword,
                }
            )
        except Exception as exc:
            logging.warning(f"Error parsing Buyee item element: {exc}")
            continue

    return items


def fetch_items(
    keyword: str,
    conn: sqlite3.Connection,
    rate: float,
    translator: TranslationService,
    session=None,
) -> list[NotificationItem]:
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
    except requests.RequestException as exc:
        logging.error(f"Failed to fetch Buyee search page for keyword '{keyword}': {exc}")
        return []

    logging.info(f"Buyee session cookies after fetch: {session.cookies.get_dict()}")

    soup = BeautifulSoup(response.text, "html.parser")
    items = extract_items_from_search_html(soup, keyword)

    if not items:
        item_lists_count = len(soup.select("ul.item-lists"))
        mercari_link_count = len(soup.select('a[href*="/mercari/item/"]'))
        logging.warning(
            f"No item elements found for keyword '{keyword}'. "
            f"item-lists={item_lists_count}, mercari-links={mercari_link_count}"
        )
        return []

    info_logger.info(f"Found {len(items)} potential items for keyword: {keyword}")

    new_items: list[NotificationItem] = []
    for item in items:
        try:
            item_id = item["id"]
            formatted_price, numeric_price = convert_price_to_yen(item["price"], rate)
            logging.debug(
                f"Processing item: {item['title']} | Raw price: {item['price']} | "
                f"Formatted: {formatted_price} | Numeric: {numeric_price}"
            )

            if not formatted_price or not numeric_price:
                logging.debug(
                    f"Skipping item due to price conversion issue: {item['title']} "
                    f"(Price text: {item['price']})"
                )
                continue

            if not item["url"] or not item["image_url"]:
                logging.debug(f"Skipping item due to missing URL or image: {item['title']}")
                continue

            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row = get_seen_item(conn, item_id)

            if row is None or numeric_price < row[0]:
                display_title = translator.translate_title_with_fallback(item["title"])
                new_items.append(
                    {
                        "title": display_title,
                        "url": item["url"],
                        "image_url": item["image_url"],
                        "price": formatted_price,
                        "item_id": item_id,
                        "numeric_price": numeric_price,
                        "keyword": keyword,
                        "timestamp": timestamp,
                    }
                )
                upsert_seen_item(
                    conn,
                    item_id,
                    numeric_price,
                    timestamp,
                    title=item["title"],
                    url=item["url"],
                )
                if row is None:
                    info_logger.info(f"New item found: {item['title']} at {formatted_price}")
                else:
                    info_logger.info(f"Cheaper item found: {item['title']} at {formatted_price}")
            else:
                logging.debug(
                    f"Item already seen: {item['title']} | Stored: {row[0]} | Current: {numeric_price}"
                )
        except Exception as exc:
            logging.warning(f"Error processing item: {exc}")
            continue

    if not new_items:
        info_logger.info(f"✅ No new items found for keyword: {keyword}")
    else:
        info_logger.info(f"📦 Found {len(new_items)} new/cheaper items for keyword: {keyword}")

    return new_items


def test_url_accessibility(url: str, timeout: int = 5) -> bool:
    try:
        response = requests.head(url, headers=DEFAULT_BUYEE_HEADERS, timeout=timeout)
        return response.status_code == 200
    except Exception as exc:
        logging.debug(f"URL accessibility test failed for {url}: {exc}")
        return False
