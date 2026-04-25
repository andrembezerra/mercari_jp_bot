import sqlite3
import unittest
from pathlib import Path
from unittest import mock

import requests
from bs4 import BeautifulSoup

from src import scraper


def make_test_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE seen_items (
            item_id    TEXT    PRIMARY KEY,
            price      INTEGER NOT NULL,
            title      TEXT,
            url        TEXT,
            first_seen TEXT    NOT NULL,
            last_seen  TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE item_suppressions (
            item_id    TEXT PRIMARY KEY,
            reason     TEXT NOT NULL,
            title      TEXT,
            url        TEXT,
            keyword    TEXT,
            created_at TEXT NOT NULL,
            removed_at TEXT
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
        session = scraper.create_buyee_session()

        self.assertEqual(session.headers["Referer"], "https://buyee.jp/mercari/")
        self.assertIn("Mozilla/5.0", session.headers["User-Agent"])
        self.assertIn("text/html", session.headers["Accept"])
        self.assertEqual(session.headers["Accept-Language"], "en-US,en;q=0.9")

    def test_extract_items_from_current_search_html(self):
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        soup = BeautifulSoup(html, "html.parser")

        items = scraper.extract_items_from_search_html(soup, "test")

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

        response = scraper.fetch_with_retry(session, "https://buyee.jp/mercari/search?keyword=test", delay=0)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 2)

    def test_fetch_with_retry_raises_on_403(self):
        session = DummySession([
            DummyResponse(status_code=403, url="https://buyee.jp/mercari/search?keyword=test"),
        ])

        with self.assertRaises(requests.HTTPError):
            scraper.fetch_with_retry(session, "https://buyee.jp/mercari/search?keyword=test", max_retries=1, delay=0)

    def test_fetch_items_does_not_depend_on_iframe(self):
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        session = DummySession([
            DummyResponse(status_code=200, url="https://buyee.jp/mercari/search?keyword=test", text=html),
        ])

        conn = make_test_db()
        translator = mock.Mock()
        translator.translate_title_with_fallback.side_effect = lambda title: title
        items = scraper.fetch_items("test", conn, rate=145.0, translator=translator, session=session)
        conn.close()

        self.assertEqual(len(items), 2)

    def test_fetch_items_skips_suppressed_items(self):
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        session = DummySession([
            DummyResponse(status_code=200, url="https://buyee.jp/mercari/search?keyword=test", text=html),
        ])
        conn = make_test_db()
        # Suppress the first fixture item (id m42393820957)
        conn.execute(
            "INSERT INTO item_suppressions (item_id, reason, title, url, keyword, created_at, removed_at) "
            "VALUES (?, 'hide', NULL, NULL, NULL, '2026-01-01 00:00:00', NULL)",
            ("m42393820957",),
        )
        conn.commit()
        translator = mock.Mock()
        translator.translate_title_with_fallback.side_effect = lambda title: title
        items = scraper.fetch_items("test", conn, rate=145.0, translator=translator, session=session)
        conn.close()
        ids = [i["item_id"] for i in items]
        self.assertNotIn("m42393820957", ids)

    def test_fetch_items_one_off_caps_at_limit_and_does_not_persist(self):
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        session = DummySession([
            DummyResponse(status_code=200, url="https://buyee.jp/mercari/search?keyword=test", text=html),
        ])
        conn = make_test_db()
        translator = mock.Mock()
        translator.translate_title_with_fallback.side_effect = lambda title: title

        items = scraper.fetch_items_one_off(
            "test", rate=145.0, translator=translator, session=session, conn=conn, limit=1
        )
        # capped at 1
        self.assertEqual(len(items), 1)
        # one-off must not write to seen_items
        seen_count = conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0]
        conn.close()
        self.assertEqual(seen_count, 0)

    def test_fetch_items_one_off_excludes_suppressed(self):
        html = FIXTURE_PATH.read_text(encoding="utf-8")
        session = DummySession([
            DummyResponse(status_code=200, url="https://buyee.jp/mercari/search?keyword=test", text=html),
        ])
        conn = make_test_db()
        conn.execute(
            "INSERT INTO item_suppressions (item_id, reason, title, url, keyword, created_at, removed_at) "
            "VALUES (?, 'hide', NULL, NULL, NULL, '2026-01-01 00:00:00', NULL)",
            ("m42393820957",),
        )
        conn.commit()
        translator = mock.Mock()
        translator.translate_title_with_fallback.side_effect = lambda title: title
        items = scraper.fetch_items_one_off(
            "test", rate=145.0, translator=translator, session=session, conn=conn
        )
        conn.close()
        ids = [i["item_id"] for i in items]
        self.assertNotIn("m42393820957", ids)


if __name__ == "__main__":
    unittest.main()
