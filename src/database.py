import configparser
import datetime
import json
import logging
import os
import sqlite3

from src.config import Settings
from src.logging_setup import info_logger


def load_keywords(config: configparser.ConfigParser) -> dict[str, str]:
    try:
        keywords_dict = dict(config.items("KEYWORDS"))
        if not keywords_dict:
            logging.critical("No keywords found in the [KEYWORDS] section of config.ini.")
            return {}
        logging.info(f"Loaded {len(keywords_dict)} keywords from config.ini.")
        return keywords_dict
    except configparser.NoSectionError:
        logging.critical("No [KEYWORDS] section found in config.ini.")
        return {}


def _migrate_json_to_db(conn: sqlite3.Connection, seen_file: str) -> None:
    if not os.path.exists(seen_file):
        return
    try:
        with open(seen_file, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            (
                item_id,
                info.get("price", 0),
                info.get("timestamp", now),
                info.get("timestamp", now),
            )
            for item_id, info in data.items()
            if isinstance(info, dict)
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO seen_items (item_id, price, first_seen, last_seen) VALUES (?,?,?,?)",
            rows,
        )
        conn.commit()
        os.rename(seen_file, seen_file + ".migrated")
        info_logger.info(f"Migrated {len(rows)} items from seen_items.json → seen_items.db")
    except Exception as exc:
        logging.warning(f"JSON migration failed (non-fatal): {exc}")


def _migrate_keywords_to_db(
    conn: sqlite3.Connection, config: configparser.ConfigParser
) -> None:
    existing = conn.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
    if existing > 0:
        return
    try:
        keywords_dict = dict(config.items("KEYWORDS"))
        rows = [(kw, label) for kw, label in keywords_dict.items()]
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO keywords (keyword, label) VALUES (?,?)", rows
            )
            conn.commit()
            info_logger.info(f"Migrated {len(rows)} keywords from config.ini to DB")
    except configparser.NoSectionError:
        pass


def init_db(
    settings: Settings,
    config: configparser.ConfigParser,
    db_path: str | None = None,
) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or settings.db_file)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_items (
            item_id    TEXT    PRIMARY KEY,
            price      INTEGER NOT NULL,
            title      TEXT,
            url        TEXT,
            first_seen TEXT    NOT NULL,
            last_seen  TEXT    NOT NULL
        )
        """
    )
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(seen_items)")}
    if "title" not in existing_cols:
        conn.execute("ALTER TABLE seen_items ADD COLUMN title TEXT")
    if "url" not in existing_cols:
        conn.execute("ALTER TABLE seen_items ADD COLUMN url TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS keywords (
            keyword TEXT PRIMARY KEY,
            label   TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id             TEXT    NOT NULL,
            keyword             TEXT    NOT NULL,
            price               INTEGER NOT NULL,
            title               TEXT,
            url                 TEXT,
            sent_at             TEXT    NOT NULL,
            telegram_message_id INTEGER
        )
        """
    )
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(notifications)")}
    if "title" not in existing_cols:
        conn.execute("ALTER TABLE notifications ADD COLUMN title TEXT")
    if "url" not in existing_cols:
        conn.execute("ALTER TABLE notifications ADD COLUMN url TEXT")
    if "telegram_message_id" not in existing_cols:
        conn.execute("ALTER TABLE notifications ADD COLUMN telegram_message_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notifications_message_id "
        "ON notifications(telegram_message_id)"
    )
    existing_kw_cols = {row[1] for row in conn.execute("PRAGMA table_info(keywords)")}
    if "disabled_at" not in existing_kw_cols:
        conn.execute("ALTER TABLE keywords ADD COLUMN disabled_at TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS item_suppressions (
            item_id    TEXT PRIMARY KEY,
            reason     TEXT NOT NULL,
            title      TEXT,
            url        TEXT,
            keyword    TEXT,
            created_at TEXT NOT NULL,
            removed_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_item_suppressions_active "
        "ON item_suppressions(removed_at)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bot_state (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    _migrate_json_to_db(conn, str(settings.seen_file))
    _migrate_keywords_to_db(conn, config)
    item_count = conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0]
    kw_count = conn.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
    info_logger.info(f"DB ready at {settings.db_file} ({item_count} items tracked, {kw_count} keywords)")
    return conn


def get_seen_item(conn: sqlite3.Connection, item_id: str):
    return conn.execute(
        "SELECT price, first_seen FROM seen_items WHERE item_id = ?",
        (item_id,),
    ).fetchone()


def upsert_seen_item(
    conn: sqlite3.Connection,
    item_id: str,
    price: int,
    timestamp: str,
    title: str | None = None,
    url: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO seen_items (item_id, price, title, url, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            price     = excluded.price,
            title     = excluded.title,
            url       = excluded.url,
            last_seen = excluded.last_seen
        """,
        (item_id, price, title, url, timestamp, timestamp),
    )
    conn.commit()


def load_keywords_from_db(
    conn: sqlite3.Connection, include_disabled: bool = False
) -> dict[str, str]:
    if include_disabled:
        rows = conn.execute("SELECT keyword, label FROM keywords").fetchall()
    else:
        rows = conn.execute(
            "SELECT keyword, label FROM keywords WHERE disabled_at IS NULL"
        ).fetchall()
    return {kw: label for kw, label in rows}


def load_skipped_keywords(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    rows = conn.execute(
        "SELECT keyword, label, disabled_at FROM keywords "
        "WHERE disabled_at IS NOT NULL ORDER BY disabled_at DESC"
    ).fetchall()
    return [(kw, label, disabled_at) for kw, label, disabled_at in rows]


def disable_keyword(conn: sqlite3.Connection, keyword: str) -> int:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "UPDATE keywords SET disabled_at = ? WHERE keyword = ? AND disabled_at IS NULL",
        (now, keyword),
    )
    conn.commit()
    return cur.rowcount


def enable_keyword(conn: sqlite3.Connection, keyword: str) -> int:
    cur = conn.execute(
        "UPDATE keywords SET disabled_at = NULL WHERE keyword = ? AND disabled_at IS NOT NULL",
        (keyword,),
    )
    conn.commit()
    return cur.rowcount


def resolve_keyword_by_label_or_keyword(
    conn: sqlite3.Connection, value: str
) -> tuple[str, str, str | None] | None:
    row = conn.execute(
        "SELECT keyword, label, disabled_at FROM keywords WHERE label = ? COLLATE NOCASE",
        (value,),
    ).fetchone()
    if row:
        return row
    row = conn.execute(
        "SELECT keyword, label, disabled_at FROM keywords WHERE keyword = ? COLLATE NOCASE",
        (value,),
    ).fetchone()
    return row


def add_keyword(conn: sqlite3.Connection, keyword: str, label: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO keywords (keyword, label) VALUES (?,?)",
        (keyword, label),
    )
    conn.commit()


def remove_keyword(conn: sqlite3.Connection, keyword: str) -> int:
    cur = conn.execute("DELETE FROM keywords WHERE keyword = ?", (keyword,))
    conn.commit()
    return cur.rowcount


def resolve_keyword_by_label(conn: sqlite3.Connection, label_filter: str):
    return conn.execute(
        "SELECT keyword FROM keywords WHERE label = ? COLLATE NOCASE",
        (label_filter,),
    ).fetchone()


def fetch_notification_counts(
    conn: sqlite3.Connection, since: str, keyword: str | None = None
):
    if keyword:
        return conn.execute(
            "SELECT keyword, COUNT(*) FROM notifications WHERE keyword = ? AND sent_at >= ? GROUP BY keyword",
            (keyword, since),
        ).fetchall()
    return conn.execute(
        "SELECT keyword, COUNT(*) FROM notifications WHERE sent_at >= ? GROUP BY keyword",
        (since,),
    ).fetchall()


def insert_notification(
    conn: sqlite3.Connection,
    item_id: str,
    keyword: str,
    price: int,
    title: str,
    url: str,
    sent_at: str,
    telegram_message_id: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO notifications "
        "(item_id, keyword, price, title, url, sent_at, telegram_message_id) "
        "VALUES (?,?,?,?,?,?,?)",
        (item_id, keyword, price, title, url, sent_at, telegram_message_id),
    )


def find_notification_by_message_id(
    conn: sqlite3.Connection, message_id: int
) -> tuple[str, str, str | None, str | None] | None:
    return conn.execute(
        "SELECT item_id, keyword, title, url FROM notifications "
        "WHERE telegram_message_id = ? ORDER BY id DESC LIMIT 1",
        (message_id,),
    ).fetchone()


def suppress_item(
    conn: sqlite3.Connection,
    item_id: str,
    reason: str,
    title: str | None,
    url: str | None,
    keyword: str | None,
) -> None:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO item_suppressions (item_id, reason, title, url, keyword, created_at, removed_at)
        VALUES (?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(item_id) DO UPDATE SET
            reason     = excluded.reason,
            title      = excluded.title,
            url        = excluded.url,
            keyword    = excluded.keyword,
            created_at = excluded.created_at,
            removed_at = NULL
        """,
        (item_id, reason, title, url, keyword, now),
    )
    conn.commit()


def unsuppress_item(conn: sqlite3.Connection, item_id: str) -> int:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "UPDATE item_suppressions SET removed_at = ? "
        "WHERE item_id = ? AND removed_at IS NULL",
        (now, item_id),
    )
    conn.commit()
    return cur.rowcount


def is_item_suppressed(conn: sqlite3.Connection, item_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM item_suppressions WHERE item_id = ? AND removed_at IS NULL",
        (item_id,),
    ).fetchone()
    return row is not None


def list_active_suppressions(
    conn: sqlite3.Connection, limit: int = 20
) -> list[tuple[str, str, str | None, str | None, str | None, str]]:
    rows = conn.execute(
        "SELECT item_id, reason, title, url, keyword, created_at "
        "FROM item_suppressions WHERE removed_at IS NULL "
        "ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [tuple(row) for row in rows]


def count_active_suppressions(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM item_suppressions WHERE removed_at IS NULL"
    ).fetchone()
    return row[0] if row else 0


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO bot_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, now),
    )
    conn.commit()


def is_paused(conn: sqlite3.Connection) -> bool:
    return get_state(conn, "paused") == "1"
