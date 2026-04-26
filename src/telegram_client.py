import logging
import sqlite3

import requests

from src.commands import (
    cmd_add_keyword,
    cmd_blocked,
    cmd_enable_keyword,
    cmd_help,
    cmd_hide,
    cmd_list_keywords,
    cmd_pause,
    cmd_remove_keyword,
    cmd_restart,
    cmd_resume,
    cmd_skip_keyword,
    cmd_skipped,
    cmd_status,
    cmd_summary,
    cmd_unblock,
    cmd_wrong,
)
from src.types import CommandContext


def _largest_photo_file_id(photos: list) -> str | None:
    if not photos:
        return None
    try:
        sized = [p for p in photos if isinstance(p, dict) and "file_id" in p]
        if not sized:
            return None
        sized.sort(key=lambda p: p.get("file_size") or p.get("width") or 0)
        return sized[-1]["file_id"]
    except Exception:
        return None


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_message(self, text: str) -> int | None:
        api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        try:
            response = requests.post(api_url, data=payload, timeout=5)
            response.raise_for_status()
            logging.info(f"📝 Sent message: {text[:50]}...")
            return response.json().get("result", {}).get("message_id")
        except requests.exceptions.RequestException as exc:
            logging.error(f"Failed to send message: {exc}")
            return None

    def send_photo(
        self, title: str, url: str, img_url: str, price: str, keyword_label: str = ""
    ) -> int | None:
        api_url = f"https://api.telegram.org/bot{self.bot_token}/sendPhoto"
        keyword_line = f"\nKeyword: {keyword_label}" if keyword_label else ""
        caption = f"<b>{title}</b>\nPrice: {price}{keyword_line}\n{url}"
        payload = {
            "chat_id": self.chat_id,
            "photo": img_url,
            "caption": caption,
            "parse_mode": "HTML",
        }
        try:
            response = requests.post(api_url, data=payload, timeout=10)
            response.raise_for_status()
            logging.info(f"Sent photo for: {title}")
            return response.json().get("result", {}).get("message_id")
        except requests.exceptions.RequestException as exc:
            logging.error(f"Failed to send photo for {title}: {exc}")
            return None

    def download_file(self, file_id: str) -> bytes | None:
        try:
            meta = requests.get(
                f"https://api.telegram.org/bot{self.bot_token}/getFile",
                params={"file_id": file_id},
                timeout=10,
            )
            meta.raise_for_status()
            file_path = meta.json().get("result", {}).get("file_path")
            if not file_path:
                return None
            file_resp = requests.get(
                f"https://api.telegram.org/file/bot{self.bot_token}/{file_path}",
                timeout=15,
            )
            file_resp.raise_for_status()
            return file_resp.content
        except requests.exceptions.RequestException as exc:
            logging.error(f"Failed to download Telegram file (status={getattr(exc.response, 'status_code', 'n/a')})")
            return None

    def check_connection(self) -> bool:
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{self.bot_token}/getMe", timeout=5
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def _build_context(self, msg: dict) -> CommandContext:
        text = (msg.get("text") or msg.get("caption") or "").strip()
        if " " in text:
            args = text.split(" ", 1)[1].strip()
        else:
            args = ""
        reply_to = msg.get("reply_to_message") or {}
        return CommandContext(
            chat_id=str(msg.get("chat", {}).get("id", "")),
            message_id=msg.get("message_id"),
            text=text,
            args=args,
            reply_to_message_id=reply_to.get("message_id"),
            photo_file_id=_largest_photo_file_id(msg.get("photo") or []),
            replied_photo_file_id=_largest_photo_file_id(reply_to.get("photo") or []),
        )

    def _dispatch(self, conn: sqlite3.Connection, ctx: CommandContext) -> None:
        text = ctx.text
        if text == "/help":
            cmd_help(self.send_message)
        elif text == "/keywords" or text.startswith("/keywords "):
            cmd_list_keywords(conn, self.send_message)
        elif text.startswith("/addkeyword "):
            cmd_add_keyword(conn, ctx.args, self.send_message)
        elif text.startswith("/removekeyword "):
            cmd_remove_keyword(conn, ctx.args, self.send_message)
        elif text == "/summary" or text.startswith("/summary "):
            cmd_summary(conn, ctx.args, self.send_message)
        elif text == "/hide" or text.startswith("/hide "):
            cmd_hide(conn, ctx, self.send_message)
        elif text == "/wrong" or text.startswith("/wrong "):
            cmd_wrong(conn, ctx, self.send_message)
        elif text == "/unblock" or text.startswith("/unblock "):
            cmd_unblock(conn, ctx, self.send_message)
        elif text == "/blocked" or text.startswith("/blocked "):
            cmd_blocked(conn, self.send_message)
        elif text == "/pause" or text.startswith("/pause "):
            cmd_pause(conn, self.send_message)
        elif text == "/resume" or text.startswith("/resume "):
            cmd_resume(conn, self.send_message)
        elif text == "/restart" or text.startswith("/restart "):
            cmd_restart(self.send_message)
        elif text == "/status" or text.startswith("/status "):
            cmd_status(conn, self.send_message)
        elif text.startswith("/skipkeyword "):
            cmd_skip_keyword(conn, ctx.args, self.send_message)
        elif text.startswith("/enablekeyword "):
            cmd_enable_keyword(conn, ctx.args, self.send_message)
        elif text == "/skipped" or text.startswith("/skipped "):
            cmd_skipped(conn, self.send_message)

    def check_commands(self, conn: sqlite3.Connection, offset: int) -> int:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{self.bot_token}/getUpdates",
                params={"offset": offset, "timeout": 0, "allowed_updates": ["message"]},
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logging.warning(f"getUpdates failed: {exc}")
            return offset

        for update in data.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message", {})
            ctx = self._build_context(msg)

            if ctx.chat_id != str(self.chat_id):
                logging.warning(f"Ignored command from unauthorised chat_id={ctx.chat_id}")
                continue

            self._dispatch(conn, ctx)

        return offset
