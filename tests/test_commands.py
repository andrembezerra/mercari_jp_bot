import configparser
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.commands import (
    cmd_add_keyword,
    cmd_blocked,
    cmd_enable_keyword,
    cmd_hide,
    cmd_pause,
    cmd_remove_keyword,
    cmd_resume,
    cmd_skip_keyword,
    cmd_skipped,
    cmd_status,
    cmd_summary,
    cmd_unblock,
    cmd_wrong,
)
from src.config import Settings
from src.database import (
    init_db,
    insert_notification,
    is_item_suppressed,
    is_paused,
    load_keywords_from_db,
)
from src.types import CommandContext


def _ctx(reply_to_message_id=None, args=""):
    return CommandContext(
        chat_id="1",
        message_id=100,
        text="/cmd",
        args=args,
        reply_to_message_id=reply_to_message_id,
        photo_file_id=None,
        replied_photo_file_id=None,
    )


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        db_path = str(Path(self.tmpdir) / "test.db")
        settings = Settings(
            base_dir=Path(self.tmpdir),
            bot_token="x",
            chat_id="1",
            config_path=Path(self.tmpdir) / "c.ini",
            db_file=Path(db_path),
            seen_file=Path(self.tmpdir) / "s.json",
            keyword_batch_delay=1,
            full_cycle_delay=1,
        )
        self.conn = init_db(settings, configparser.ConfigParser(), db_path=db_path)
        self.messages = []

    def tearDown(self):
        self.conn.close()

    def send_message(self, text):
        self.messages.append(text)

    # --- existing keyword/summary tests ---

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
            "INSERT INTO notifications (item_id, keyword, price, title, url, sent_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            ("item-1", "Nintendo Switch", 1000, "Title", "https://example.com"),
        )
        self.conn.commit()

        cmd_summary(self.conn, "Console 24h", self.send_message)
        self.assertIn("Console", self.messages[0])
        self.assertIn("1 item", self.messages[0])

    # --- moderation: /hide /wrong /unblock /blocked ---

    def _seed_notification(self, message_id=42, item_id="item-x"):
        insert_notification(
            self.conn,
            item_id,
            "Nintendo Switch",
            1000,
            "Switch Console",
            "https://buyee.jp/mercari/item/item-x",
            "2026-01-01 00:00:00",
            telegram_message_id=message_id,
        )
        self.conn.commit()

    def test_hide_requires_reply(self):
        cmd_hide(self.conn, _ctx(reply_to_message_id=None), self.send_message)
        self.assertIn("Responda a uma notificação", self.messages[0])

    def test_hide_via_reply_suppresses_item(self):
        self._seed_notification()
        cmd_hide(self.conn, _ctx(reply_to_message_id=42), self.send_message)
        self.assertTrue(is_item_suppressed(self.conn, "item-x"))
        self.assertIn("ocultado", self.messages[0])

    def test_wrong_via_reply_suppresses_with_reason(self):
        self._seed_notification()
        cmd_wrong(self.conn, _ctx(reply_to_message_id=42), self.send_message)
        self.assertTrue(is_item_suppressed(self.conn, "item-x"))
        reason = self.conn.execute(
            "SELECT reason FROM item_suppressions WHERE item_id = ?", ("item-x",)
        ).fetchone()[0]
        self.assertEqual(reason, "wrong")

    def test_unblock_removes_active_suppression(self):
        self._seed_notification()
        cmd_hide(self.conn, _ctx(reply_to_message_id=42), self.send_message)
        cmd_unblock(self.conn, _ctx(reply_to_message_id=42), self.send_message)
        self.assertFalse(is_item_suppressed(self.conn, "item-x"))
        self.assertIn("desbloqueado", self.messages[-1])

    def test_unblock_idempotent_when_never_blocked(self):
        self._seed_notification()
        cmd_unblock(self.conn, _ctx(reply_to_message_id=42), self.send_message)
        self.assertIn("não estava bloqueado", self.messages[-1])

    def test_blocked_lists_active_suppressions(self):
        self._seed_notification()
        cmd_hide(self.conn, _ctx(reply_to_message_id=42), self.send_message)
        self.messages.clear()
        cmd_blocked(self.conn, self.send_message)
        self.assertIn("Switch Console", self.messages[0])
        self.assertIn("hide", self.messages[0])

    def test_blocked_empty_state(self):
        cmd_blocked(self.conn, self.send_message)
        self.assertIn("Nenhum item bloqueado", self.messages[0])

    def test_hide_on_unknown_message_id_errors_gracefully(self):
        cmd_hide(self.conn, _ctx(reply_to_message_id=999), self.send_message)
        self.assertIn("Não consegui identificar", self.messages[0])

    # --- runtime: /pause /resume /status ---

    def test_pause_resume_persist_state(self):
        cmd_pause(self.conn, self.send_message)
        self.assertTrue(is_paused(self.conn))
        cmd_resume(self.conn, self.send_message)
        self.assertFalse(is_paused(self.conn))

    def test_status_renders_summary(self):
        self.conn.execute(
            "INSERT INTO keywords (keyword, label) VALUES (?, ?)", ("foo", "Foo")
        )
        self.conn.commit()
        cmd_status(self.conn, self.send_message)
        text = self.messages[0]
        self.assertIn("Status", text)
        self.assertIn("Keywords ativas", text)
        self.assertIn("rodando", text)

    def test_status_reflects_paused_state(self):
        cmd_pause(self.conn, self.send_message)
        self.messages.clear()
        cmd_status(self.conn, self.send_message)
        self.assertIn("pausado", self.messages[0])

    # --- keyword control: /skipkeyword /enablekeyword /skipped ---

    def test_skip_and_enable_by_label(self):
        cmd_add_keyword(self.conn, "Nintendo Switch = Console", self.send_message)
        self.messages.clear()

        cmd_skip_keyword(self.conn, "Console", self.send_message)
        self.assertIn("ignorada", self.messages[0])
        self.assertEqual(load_keywords_from_db(self.conn), {})

        cmd_enable_keyword(self.conn, "Console", self.send_message)
        self.assertIn("reativada", self.messages[-1])
        self.assertEqual(load_keywords_from_db(self.conn), {"Nintendo Switch": "Console"})

    def test_skip_by_raw_keyword(self):
        cmd_add_keyword(self.conn, "Nintendo Switch = Console", self.send_message)
        self.messages.clear()
        cmd_skip_keyword(self.conn, "Nintendo Switch", self.send_message)
        self.assertIn("ignorada", self.messages[0])

    def test_skip_unknown_keyword(self):
        cmd_skip_keyword(self.conn, "nope", self.send_message)
        self.assertIn("não encontrada", self.messages[0])

    def test_skipped_lists_disabled_keywords(self):
        cmd_add_keyword(self.conn, "Nintendo Switch = Console", self.send_message)
        cmd_skip_keyword(self.conn, "Console", self.send_message)
        self.messages.clear()
        cmd_skipped(self.conn, self.send_message)
        self.assertIn("Console", self.messages[0])

    def test_skipped_empty_state(self):
        cmd_skipped(self.conn, self.send_message)
        self.assertIn("Nenhuma keyword ignorada", self.messages[0])


if __name__ == "__main__":
    unittest.main()
