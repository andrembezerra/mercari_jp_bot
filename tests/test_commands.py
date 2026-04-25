import sqlite3
import unittest

from src.commands import cmd_add_keyword, cmd_remove_keyword, cmd_summary


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE keywords (keyword TEXT PRIMARY KEY, label TEXT NOT NULL)")
        self.conn.execute(
            """
            CREATE TABLE notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                keyword TEXT NOT NULL,
                price INTEGER NOT NULL,
                title TEXT,
                url TEXT,
                sent_at TEXT NOT NULL
            )
            """
        )
        self.messages = []

    def tearDown(self):
        self.conn.close()

    def send_message(self, text):
        self.messages.append(text)

    def test_add_keyword_with_label(self):
        cmd_add_keyword(self.conn, "Nintendo Switch = Console", self.send_message)

        row = self.conn.execute("SELECT keyword, label FROM keywords").fetchone()
        self.assertEqual(row, ("Nintendo Switch", "Console"))
        self.assertIn("Keyword adicionada", self.messages[0])

    def test_remove_keyword_missing(self):
        cmd_remove_keyword(self.conn, "missing", self.send_message)

        self.assertIn("Keyword não encontrada", self.messages[0])

    def test_summary_filters_by_label(self):
        self.conn.execute(
            "INSERT INTO keywords (keyword, label) VALUES (?, ?)",
            ("Nintendo Switch", "Console"),
        )
        self.conn.execute(
            "INSERT INTO notifications (item_id, keyword, price, title, url, sent_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("item-1", "Nintendo Switch", 1000, "Title", "https://example.com"),
        )
        self.conn.commit()

        cmd_summary(self.conn, "Console 24h", self.send_message)

        self.assertIn("Console", self.messages[0])
        self.assertIn("1 item", self.messages[0])


if __name__ == "__main__":
    unittest.main()
