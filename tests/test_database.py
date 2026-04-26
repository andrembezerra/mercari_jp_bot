import configparser
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.config import Settings
from src.database import (
    disable_keyword,
    enable_keyword,
    find_notification_by_message_id,
    get_state,
    init_db,
    insert_notification,
    is_item_suppressed,
    is_paused,
    load_keywords_from_db,
    load_skipped_keywords,
    set_state,
    suppress_item,
    unsuppress_item,
)


class DatabaseTests(unittest.TestCase):
    def test_init_db_creates_tables_and_migrates_keywords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            settings = Settings(
                base_dir=base_dir,
                bot_token="token",
                chat_id="123",
                config_path=base_dir / "config.ini",
                db_file=base_dir / "seen_items.db",
                seen_file=base_dir / "seen_items.json",
                keyword_batch_delay=10,
                full_cycle_delay=60,
            )
            config = configparser.ConfigParser()
            config.add_section("KEYWORDS")
            config.set("KEYWORDS", "Nintendo Switch", "Nintendo Switch")

            conn = init_db(settings, config)
            self.addCleanup(conn.close)

            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            self.assertIn("seen_items", tables)
            self.assertIn("keywords", tables)
            self.assertIn("notifications", tables)
            self.assertEqual(
                load_keywords_from_db(conn),
                {"nintendo switch": "Nintendo Switch"},
            )

    def test_init_db_adds_missing_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            db_path = base_dir / "legacy.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE seen_items (item_id TEXT PRIMARY KEY, price INTEGER NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, keyword TEXT NOT NULL, price INTEGER NOT NULL, sent_at TEXT NOT NULL)"
            )
            conn.commit()
            conn.close()

            settings = Settings(
                base_dir=base_dir,
                bot_token="token",
                chat_id="123",
                config_path=base_dir / "config.ini",
                db_file=db_path,
                seen_file=base_dir / "seen_items.json",
                keyword_batch_delay=10,
                full_cycle_delay=60,
            )
            config = configparser.ConfigParser()
            opened = init_db(settings, config)
            self.addCleanup(opened.close)

            seen_cols = {row[1] for row in opened.execute("PRAGMA table_info(seen_items)")}
            notif_cols = {row[1] for row in opened.execute("PRAGMA table_info(notifications)")}
            self.assertIn("title", seen_cols)
            self.assertIn("url", seen_cols)
            self.assertIn("title", notif_cols)
            self.assertIn("url", notif_cols)


class NewSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base_dir = Path(self.tmp.name)
        self.settings = Settings(
            base_dir=base_dir,
            bot_token="token",
            chat_id="123",
            config_path=base_dir / "config.ini",
            db_file=base_dir / "seen_items.db",
            seen_file=base_dir / "seen_items.json",
            keyword_batch_delay=10,
            full_cycle_delay=60,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _open(self):
        return init_db(self.settings, configparser.ConfigParser())

    def test_init_db_is_idempotent(self):
        conn = self._open()
        conn.close()
        conn = self._open()
        self.addCleanup(conn.close)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        self.assertIn("item_suppressions", tables)
        self.assertIn("bot_state", tables)

    def test_suppress_unsuppress_roundtrip(self):
        conn = self._open()
        self.addCleanup(conn.close)
        suppress_item(conn, "i1", "hide", "T", "U", "kw")
        self.assertTrue(is_item_suppressed(conn, "i1"))
        # repeat suppression is idempotent
        suppress_item(conn, "i1", "wrong", "T", "U", "kw")
        self.assertTrue(is_item_suppressed(conn, "i1"))
        unsuppress_item(conn, "i1")
        self.assertFalse(is_item_suppressed(conn, "i1"))
        # history preserved
        rows = conn.execute("SELECT COUNT(*) FROM item_suppressions").fetchone()[0]
        self.assertEqual(rows, 1)

    def test_paused_state_persists_across_reopen(self):
        conn = self._open()
        set_state(conn, "paused", "1")
        conn.close()
        conn2 = self._open()
        self.addCleanup(conn2.close)
        self.assertTrue(is_paused(conn2))
        self.assertEqual(get_state(conn2, "paused"), "1")

    def test_disable_enable_keyword(self):
        conn = self._open()
        self.addCleanup(conn.close)
        conn.execute(
            "INSERT INTO keywords (keyword, label) VALUES (?, ?)", ("foo", "Foo")
        )
        conn.commit()
        disable_keyword(conn, "foo")
        self.assertEqual(load_keywords_from_db(conn), {})
        skipped = load_skipped_keywords(conn)
        self.assertEqual(len(skipped), 1)
        enable_keyword(conn, "foo")
        self.assertEqual(load_keywords_from_db(conn), {"foo": "Foo"})

    def test_find_notification_by_message_id(self):
        conn = self._open()
        self.addCleanup(conn.close)
        insert_notification(
            conn, "i1", "kw", 1000, "T", "U", "2026-01-01 00:00:00",
            telegram_message_id=42,
        )
        conn.commit()
        row = find_notification_by_message_id(conn, 42)
        self.assertEqual(row, ("i1", "kw", "T", "U"))
        self.assertIsNone(find_notification_by_message_id(conn, 999))


if __name__ == "__main__":
    unittest.main()
