import configparser
import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*_args, **_kwargs):
        return False


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    bot_token: str | None
    chat_id: str | None
    config_path: Path
    db_file: Path
    seen_file: Path
    keyword_batch_delay: int
    full_cycle_delay: int


def load_settings(base_dir: Path | None = None) -> tuple[Settings, configparser.ConfigParser]:
    base_dir = base_dir or Path(__file__).resolve().parent.parent

    load_dotenv(base_dir / "key.env")
    config = configparser.ConfigParser()
    config_path = base_dir / "config.ini"
    if config_path.exists():
        config.read(config_path, encoding="utf-8")

    db_file_name = (
        config.get("BOT_SETTINGS", "DB_FILE", fallback="seen_items.db")
        if config.has_section("BOT_SETTINGS")
        else "seen_items.db"
    )

    settings = Settings(
        base_dir=base_dir,
        bot_token=os.getenv("BOT_TOKEN"),
        chat_id=os.getenv("CHAT_ID"),
        config_path=config_path,
        db_file=base_dir / db_file_name,
        seen_file=base_dir / "seen_items.json",
        keyword_batch_delay=(
            config.getint("DELAYS", "KEYWORD_BATCH_DELAY", fallback=10)
            if config.has_section("DELAYS")
            else 10
        ),
        full_cycle_delay=(
            config.getint("DELAYS", "FULL_CYCLE_DELAY", fallback=60)
            if config.has_section("DELAYS")
            else 60
        ),
    )
    return settings, config


def validate_config(settings: Settings, config: configparser.ConfigParser) -> None:
    if not settings.bot_token or not settings.chat_id:
        raise ValueError("Missing Telegram credentials in key.env")

    try:
        int(settings.chat_id)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CHAT_ID must be a valid integer, got: {settings.chat_id!r}") from exc

    if not settings.config_path.exists():
        raise ValueError(f"Configuration file '{settings.config_path}' not found")

    for section in ["BOT_SETTINGS", "DELAYS"]:
        if not config.has_section(section):
            raise ValueError(f"Missing required section: {section}")
