import configparser
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.config import Settings
from src.database import init_db
from src.image_search import (
    OcrResult,
    TesseractEngine,
    _build_ocr_variants,
    normalize_ocr_tokens,
    run_image_search,
    run_ocr_pipeline,
    select_best_keyword,
)
from src.types import CommandContext


class CannedEngine:
    def __init__(self, text):
        self.text = text

    def extract(self, image_bytes):
        return self.text


class ErrorEngine:
    def __init__(self, message):
        self.last_error_message = message

    def extract(self, image_bytes):
        return ""


class FakeClient:
    def __init__(self, image_bytes=b"fake-bytes"):
        self.image_bytes = image_bytes
        self.messages = []
        self.photos = []

    def send_message(self, text):
        self.messages.append(text)

    def send_photo(self, title, url, img_url, price, keyword_label=""):
        self.photos.append({"title": title, "label": keyword_label})

    def download_file(self, file_id):
        return self.image_bytes


def _photo_ctx(photo_file_id="abc"):
    return CommandContext(
        chat_id="1",
        message_id=1,
        text="/imagesearch",
        args="",
        reply_to_message_id=2,
        photo_file_id=None,
        replied_photo_file_id=photo_file_id,
    )


class OcrPipelineTests(unittest.TestCase):
    def test_normalize_strips_punctuation_and_short_latin(self):
        text = "!!! ::: PRODUCT  https://x.example  ニンテンドースイッチ Hi 12"
        tokens = normalize_ocr_tokens(text)
        self.assertIn("ニンテンドースイッチ", tokens)
        self.assertIn("PRODUCT", tokens)
        self.assertNotIn("Hi", tokens)
        self.assertNotIn("12", tokens)

    def test_normalize_dedupes(self):
        tokens = normalize_ocr_tokens("任天堂 任天堂 任天堂スイッチ")
        self.assertEqual(tokens.count("任天堂"), 1)

    def test_normalize_empty(self):
        self.assertEqual(normalize_ocr_tokens(""), [])
        self.assertEqual(normalize_ocr_tokens("!!! ::: 12"), [])

    def test_select_best_prefers_kanji_katakana_over_latin(self):
        terms = ["PRODUCT", "ニンテンドースイッチ", "任天堂", "Hello"]
        self.assertEqual(select_best_keyword(terms), "ニンテンドースイッチ")

    def test_select_best_empty(self):
        self.assertEqual(select_best_keyword([]), "")

    def test_run_pipeline_with_canned_engine(self):
        result = run_ocr_pipeline(b"x", engine=CannedEngine("任天堂スイッチ 本体"))
        self.assertEqual(result.selected_keyword, "任天堂スイッチ")
        self.assertIn("任天堂スイッチ", result.candidate_terms)

    def test_run_pipeline_preserves_engine_error_message(self):
        result = run_ocr_pipeline(
            b"x",
            engine=ErrorEngine("❌ OCR indisponível no momento."),
        )
        self.assertEqual(result.error_message, "❌ OCR indisponível no momento.")

    def test_build_ocr_variants_returns_multiple_processed_images(self):
        from PIL import Image, ImageFilter, ImageOps

        image = Image.new("RGB", (120, 80), color="white")
        variants = _build_ocr_variants(image, ImageOps, ImageFilter)

        self.assertEqual(len(variants), 4)
        self.assertTrue(all(variant.mode == "L" for variant in variants))
        self.assertTrue(all(variant.width >= 240 for variant in variants))

    def test_tesseract_engine_tries_multiple_attempts(self):
        fake_pytesseract = mock.Mock()
        fake_pytesseract.image_to_string.side_effect = [
            "!!! ::: 12",
            "任天堂スイッチ",
        ]

        with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
            from PIL import Image

            Image.new("RGB", (40, 20), color="white").save(tmp.name)
            tmp.seek(0)

            fake_module = sys.modules.get("pytesseract")
            sys.modules["pytesseract"] = fake_pytesseract
            try:
                engine = TesseractEngine()
                text = engine.extract(Path(tmp.name).read_bytes())
            finally:
                if fake_module is None:
                    sys.modules.pop("pytesseract", None)
                else:
                    sys.modules["pytesseract"] = fake_module

        self.assertEqual(text, "任天堂スイッチ")
        self.assertGreaterEqual(fake_pytesseract.image_to_string.call_count, 2)


class ImageSearchOrchestratorTests(unittest.TestCase):
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

    def tearDown(self):
        self.conn.close()

    def test_no_replied_photo_errors_gracefully(self):
        client = FakeClient()
        ctx = CommandContext(
            chat_id="1",
            message_id=1,
            text="/imagesearch",
            args="",
            reply_to_message_id=None,
            photo_file_id=None,
            replied_photo_file_id=None,
        )
        run_image_search(self.conn, ctx, client, engine=CannedEngine("x"))
        self.assertIn("Responda a uma foto", client.messages[0])

    def test_download_failure_errors_gracefully(self):
        client = FakeClient(image_bytes=None)
        run_image_search(
            self.conn, _photo_ctx(), client, engine=CannedEngine("x")
        )
        self.assertIn("Não consegui baixar", client.messages[0])

    def test_no_text_in_image_errors_gracefully(self):
        client = FakeClient()
        run_image_search(
            self.conn, _photo_ctx(), client, engine=CannedEngine("!!! ::: 12")
        )
        self.assertIn("OCR não encontrou", client.messages[0])

    def test_ocr_dependency_error_is_reported_directly(self):
        client = FakeClient()
        run_image_search(
            self.conn,
            _photo_ctx(),
            client,
            engine=ErrorEngine("❌ OCR indisponível no momento."),
        )
        self.assertIn("OCR indisponível", client.messages[0])

    def test_full_flow_sends_results_and_suggestion(self):
        client = FakeClient()
        items = [
            {
                "title": f"Item {n}",
                "url": f"https://buyee.jp/mercari/item/{n}",
                "image_url": f"https://example.com/{n}.jpg",
                "price": "¥1,000",
                "item_id": f"item-{n}",
                "numeric_price": 1000,
                "keyword": "任天堂スイッチ",
                "timestamp": "2026-01-01 00:00:00",
            }
            for n in range(25)
        ]

        with mock.patch(
            "src.image_search.fetch_items_one_off", return_value=items[:20]
        ) as fetch_mock, mock.patch(
            "src.image_search.create_buyee_session", return_value=mock.Mock()
        ), mock.patch(
            "src.image_search.TranslationService"
        ), mock.patch(
            "src.image_search.ExchangeRateService"
        ) as rate_mock, mock.patch(
            "src.image_search.time.sleep"
        ):
            rate_mock.return_value.get_exchange_rate_with_fallback.return_value = 145.0
            run_image_search(
                self.conn,
                _photo_ctx(),
                client,
                engine=CannedEngine("任天堂スイッチ"),
            )

        self.assertTrue(fetch_mock.called)
        self.assertEqual(fetch_mock.call_args.kwargs.get("limit"), 20)
        self.assertEqual(len(client.photos), 20)
        self.assertTrue(
            any("/addkeyword" in m for m in client.messages),
            f"messages: {client.messages}",
        )
        self.assertTrue(
            any("OCR busca" in m for m in client.messages),
            f"messages: {client.messages}",
        )

    def test_no_results_sends_friendly_message(self):
        client = FakeClient()
        with mock.patch(
            "src.image_search.fetch_items_one_off", return_value=[]
        ), mock.patch(
            "src.image_search.create_buyee_session", return_value=mock.Mock()
        ), mock.patch(
            "src.image_search.TranslationService"
        ), mock.patch(
            "src.image_search.ExchangeRateService"
        ) as rate_mock:
            rate_mock.return_value.get_exchange_rate_with_fallback.return_value = 145.0
            run_image_search(
                self.conn,
                _photo_ctx(),
                client,
                engine=CannedEngine("任天堂スイッチ"),
            )
        self.assertTrue(any("Nenhum resultado" in m for m in client.messages))


if __name__ == "__main__":
    unittest.main()
