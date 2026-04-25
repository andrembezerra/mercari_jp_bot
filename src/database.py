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
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id  TEXT    NOT NULL,
            keyword  TEXT    NOT NULL,
            price    INTEGER NOT NULL,
            title    TEXT,
            url      TEXT,
            sent_at  TEXT    NOT NULL
        )
        """
    )
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(notifications)")}
    if "title" not in existing_cols:
        conn.execute("ALTER TABLE notifications ADD COLUMN title TEXT")
    if "url" not in existing_cols:
        conn.execute("ALTER TABLE notifications ADD COLUMN url TEXT")
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


def load_keywords_from_db(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT keyword, label FROM keywords").fetchall()
    return {kw: label for kw, label in rows}


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
) -> None:
    conn.execute(
        "INSERT INTO notifications (item_id, keyword, price, title, url, sent_at) VALUES (?,?,?,?,?,?)",
        (item_id, keyword, price, title, url, sent_at),
    )
