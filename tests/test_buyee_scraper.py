import sqlite3
import unittest
from pathlib import Path
from unittest import mock

import requests
from bs4 import BeautifulSoup

import mercari_telegram_bot_config_improved as bot


def make_test_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the seen_items schema for tests."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE seen_items (
            item_id    TEXT    PRIMARY KEY,
            price      INTEGER NOT NULL,
            first_seen TEXT    NOT NULL,
            last_seen  TEXT    NOT NULL
        )
    """)
    conn.commit()
    return conn


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "buyee_search_sample.html"


class DummyResponse:
    def __init__(self, status_code=200, url="https://buyee.jp/test", text="ok"):
        self.status_code = status_code
        self.url = url
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for {self.url}")


class DummySession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.cookies = requests.cookies.RequestsCookieJar()

    def get(self, url, headers=None, timeout=30):
        self.calls.append({
            "url": url,
            "headers": headers,
            "timeout": timeout,
        })
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class BuyeeScraperTests(unittest.TestCase):
    def test_create_buyee_session_sets_browser_headers(self):
        session = bot.create_buyee_session()

        self.assertEqual(session.headers["Referer"], "https://buyee.jp/mercari/")
        self.assertIn("Mozilla/5.0", session.headers["User-Agent"])
        self.assertIn("text/html", session.headers["Accept"])
        self.assertEqual(session.headers["Accept-Language"], "en-US,en;q=0.9")

    def test_extract_items_from_current_search_html(self):
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")

        with mock.patch.object(bot, "translate_title_with_fallback", side_effect=lambda title: title):
            items = bot._extract_items_from_search_html(soup, "test")

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], "m42393820957")
        self.assertEqual(items[0]["price"], "333 YEN")
        self.assertEqual(items[0]["url"], "https://buyee.jp/mercari/item/m42393820957?conversionType=Mercari_DirectSearch")
        self.assertEqual(items[0]["image_url"], "https://static.mercdn.net/thumb/item/jpeg/m42393820957_1.jpg?1737861593")

    def test_fetch_with_retry_retries_then_succeeds(self):
        session = DummySession([
            requests.ConnectionError("temporary error"),
            DummyResponse(status_code=200, url="https://buyee.jp/mercari/search?keyword=test"),
        ])

        response = bot.fetch_with_retry(session, "https://buyee.jp/mercari/search?keyword=test", delay=0)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 2)

    def test_fetch_with_retry_raises_on_403(self):
        session = DummySession([
            DummyResponse(status_code=403, url="https://buyee.jp/mercari/search?keyword=test"),
        ])

        with self.assertRaises(requests.HTTPError):
            bot.fetch_with_retry(session, "https://buyee.jp/mercari/search?keyword=test", max_retries=1, delay=0)

    def test_fetch_items_does_not_depend_on_iframe(self):
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        session = DummySession([
            DummyResponse(status_code=200, url="https://buyee.jp/mercari/search?keyword=test", text=html),
        ])

        conn = make_test_db()
        with mock.patch.object(bot, "translate_title_with_fallback", side_effect=lambda title: title):
            items = bot.fetch_items("test", conn, rate=145.0, session=session)
        conn.close()

        self.assertEqual(len(items), 2)


if __name__ == "__main__":
    unittest.main()
