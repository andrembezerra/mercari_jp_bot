import configparser
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.config import load_settings, validate_config


class ConfigTests(unittest.TestCase):
    def test_validate_config_requires_sections(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            (base_dir / "config.ini").write_text("[BOT_SETTINGS]\nDB_FILE=seen_items.db\n", encoding="utf-8")

            with mock.patch.dict("os.environ", {"BOT_TOKEN": "token", "CHAT_ID": "123"}, clear=False):
                settings, config = load_settings(base_dir=base_dir)

            with self.assertRaises(ValueError) as ctx:
                validate_config(settings, config)

            self.assertIn("Missing required section: DELAYS", str(ctx.exception))

    def test_validate_config_requires_numeric_chat_id(self):
        config = configparser.ConfigParser()
        config.add_section("BOT_SETTINGS")
        config.add_section("DELAYS")

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            (base_dir / "config.ini").write_text("[BOT_SETTINGS]\nDB_FILE=seen_items.db\n[DELAYS]\nKEYWORD_BATCH_DELAY=1\nFULL_CYCLE_DELAY=2\n", encoding="utf-8")
            with mock.patch.dict("os.environ", {"BOT_TOKEN": "token", "CHAT_ID": "abc"}, clear=False):
                settings, loaded_config = load_settings(base_dir=base_dir)

            with self.assertRaises(ValueError) as ctx:
                validate_config(settings, loaded_config)

            self.assertIn("CHAT_ID must be a valid integer", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
