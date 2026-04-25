import logging
import sqlite3

import requests

from src.commands import (
    cmd_add_keyword,
    cmd_help,
    cmd_list_keywords,
    cmd_remove_keyword,
    cmd_summary,
)


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_message(self, text: str) -> None:
        api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        try:
            response = requests.post(api_url, data=payload, timeout=5)
            response.raise_for_status()
            logging.info(f"📝 Sent message: {text[:50]}...")
        except requests.exceptions.RequestException as exc:
            logging.error(f"Failed to send message: {exc}")

    def send_photo(
        self, title: str, url: str, img_url: str, price: str, keyword_label: str = ""
    ) -> None:
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
        except requests.exceptions.RequestException as exc:
            logging.error(f"Failed to send photo for {title}: {exc}")

    def check_connection(self) -> bool:
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{self.bot_token}/getMe", timeout=5
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

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
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "").strip()

            if chat_id != str(self.chat_id):
                logging.warning(f"Ignored command from unauthorised chat_id={chat_id}")
                continue

            if text == "/help":
                cmd_help(self.send_message)
            elif text == "/keywords" or text.startswith("/keywords "):
                cmd_list_keywords(conn, self.send_message)
            elif text.startswith("/addkeyword "):
                cmd_add_keyword(conn, text[len("/addkeyword "):].strip(), self.send_message)
            elif text.startswith("/removekeyword "):
                cmd_remove_keyword(conn, text[len("/removekeyword "):].strip(), self.send_message)
            elif text == "/summary" or text.startswith("/summary "):
                cmd_summary(conn, text[len("/summary"):].strip(), self.send_message)

        return offset
