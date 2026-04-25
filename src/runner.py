import logging
import os
import sys
import time

try:
    import psutil
except ModuleNotFoundError:
    psutil = None

from src.config import load_settings, validate_config
from src.database import init_db, insert_notification, load_keywords_from_db
from src.logging_setup import info_logger
from src.pricing import ExchangeRateService
from src.scraper import create_buyee_session, fetch_items
from src.telegram_client import TelegramClient
from src.translation import TranslationService


def log_memory():
    if psutil is None:
        logging.info("Memory usage unavailable: psutil is not installed")
        return
    process = psutil.Process(os.getpid())
    logging.info(f"Memory usage: {process.memory_info().rss / 1024 ** 2:.2f} MB")


def main():
    settings, config = load_settings()

    try:
        validate_config(settings, config)
        info_logger.info("✅ Configuration validation passed")
    except ValueError as exc:
        logging.critical(f"Configuration error: {exc}")
        sys.exit(1)

    telegram = TelegramClient(settings.bot_token, settings.chat_id)
    if not telegram.check_connection():
        logging.critical("❌ Cannot connect to Telegram API. Please check your BOT_TOKEN.")
        sys.exit(1)

    info_logger.info("✅ Telegram connection verified")
    info_logger.info("🚀 Mercari bot is starting...")

    conn = None
    try:
        conn = init_db(settings, config)
    except Exception as exc:
        logging.critical(f"Failed to initialise DB: {exc}")
        sys.exit(1)

    keywords_map = load_keywords_from_db(conn)
    if not keywords_map:
        telegram.send_message(
            "⚠️ Nenhuma keyword cadastrada.\n\n"
            "Use /addkeyword &lt;keyword&gt; = &lt;label&gt; para adicionar."
        )
        info_logger.info("No keywords in DB yet — bot will wait for /addkeyword commands.")

    info_logger.info(f"📋 {len(keywords_map)} keyword(s) loaded from DB")

    rate_service = ExchangeRateService()
    rate = rate_service.get_exchange_rate_with_fallback()
    translator = TranslationService()
    buyee_session = create_buyee_session()
    telegram_offset = 0

    try:
        while True:
            telegram_offset = telegram.check_commands(conn, telegram_offset)
            keywords_map = load_keywords_from_db(conn)

            for kw_original, kw_translated in keywords_map.items():
                try:
                    info_logger.info(
                        f"🔍 Starting search for keyword: {kw_original} (Translated: {kw_translated})"
                    )
                    items = fetch_items(
                        kw_original,
                        conn,
                        rate,
                        translator=translator,
                        session=buyee_session,
                    )

                    if items:
                        info_logger.info(
                            f"Sending {len(items)} items to Telegram for keyword: {kw_original}"
                        )
                        items.reverse()
                        for item in items:
                            telegram.send_photo(
                                item["title"],
                                item["url"],
                                item["image_url"],
                                item["price"],
                                keyword_label=kw_translated,
                            )
                            insert_notification(
                                conn,
                                item["item_id"],
                                kw_original,
                                item["numeric_price"],
                                item["title"],
                                item["url"],
                                item["timestamp"],
                            )
                        conn.commit()
                    else:
                        logging.info(f"No new items found for keyword: {kw_original}")

                except Exception as exc:
                    logging.error(f"Error processing keyword '{kw_original}': {exc}")
                    continue

                time.sleep(settings.keyword_batch_delay)

            info_logger.info("✅ Finished a full cycle of keyword searches. Waiting for next cycle...")
            time.sleep(settings.full_cycle_delay)

    except KeyboardInterrupt:
        info_logger.info("🛑 Bot stopped by user (KeyboardInterrupt).")
    except Exception as exc:
        logging.critical(f"An unhandled critical error occurred: {exc}", exc_info=True)
        try:
            telegram.send_message(f"❗️ An error occurred: {exc}")
        except Exception:
            logging.error("Failed to send error notification to Telegram")
        logging.error("Shutting down due to critical error.")
    finally:
        if conn:
            conn.close()
        try:
            telegram.send_message("🔴 Mercari bot has stopped.")
        except Exception:
            logging.error("Failed to send shutdown notification to Telegram")
        info_logger.info("🔴 Mercari bot is shutting down.")
