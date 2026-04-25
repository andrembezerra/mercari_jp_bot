import configparser
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.config import Settings
from src.database import init_db, load_keywords_from_db


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


if __name__ == "__main__":
    unittest.main()
