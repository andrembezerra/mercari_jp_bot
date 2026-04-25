import logging
import re
import sqlite3
import time
from dataclasses import dataclass, field
from html import escape
from typing import Protocol

from src.logging_setup import info_logger
from src.pricing import ExchangeRateService
from src.scraper import create_buyee_session, fetch_items_one_off
from src.translation import TranslationService
from src.types import CommandContext

_KANJI_RANGE = r"一-鿿"
_HIRAGANA_RANGE = r"぀-ゟ"
_KATAKANA_RANGE = r"゠-ヿㇰ-ㇿ"
_FULLWIDTH_ALNUM = r"０-９Ａ-Ｚａ-ｚ"

_JP_TOKEN_RE = re.compile(
    rf"[{_KANJI_RANGE}{_HIRAGANA_RANGE}{_KATAKANA_RANGE}{_FULLWIDTH_ALNUM}A-Za-z0-9]+"
)
_KANJI_RE = re.compile(rf"[{_KANJI_RANGE}]")
_KATAKANA_RE = re.compile(rf"[{_KATAKANA_RANGE}]")

_NOISE_PATTERNS = [
    re.compile(r"https?://\S+"),
    re.compile(r"[!-/:-@\[-`{-~]+"),
]


@dataclass(frozen=True)
class OcrResult:
    raw_text: str
    candidate_terms: list[str] = field(default_factory=list)
    selected_keyword: str = ""


class OcrEngine(Protocol):
    def extract(self, image_bytes: bytes) -> str: ...


class TesseractEngine:
    def extract(self, image_bytes: bytes) -> str:
        try:
            import io

            import pytesseract
            from PIL import Image
        except ImportError as exc:
            logging.error(f"OCR dependency missing: {exc}")
            return ""

        try:
            image = Image.open(io.BytesIO(image_bytes))
            return pytesseract.image_to_string(image, lang="jpn+eng")
        except Exception as exc:
            logging.warning(f"Tesseract OCR failed: {exc}")
            return ""


def normalize_ocr_tokens(text: str) -> list[str]:
    if not text:
        return []
    cleaned = text
    for pattern in _NOISE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    tokens = _JP_TOKEN_RE.findall(cleaned)
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if len(token) < 2:
            continue
        is_japanese = bool(
            _KANJI_RE.search(token) or _KATAKANA_RE.search(token)
        )
        if not is_japanese and len(token) < 4:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _score_token(token: str) -> int:
    kanji_count = len(_KANJI_RE.findall(token))
    kata_count = len(_KATAKANA_RE.findall(token))
    return kanji_count * 4 + kata_count * 2 + min(len(token), 12)


def select_best_keyword(terms: list[str]) -> str:
    if not terms:
        return ""
    return max(terms, key=_score_token)


def run_ocr_pipeline(image_bytes: bytes, engine: OcrEngine | None = None) -> OcrResult:
    engine = engine or TesseractEngine()
    raw_text = engine.extract(image_bytes)
    candidates = normalize_ocr_tokens(raw_text)
    selected = select_best_keyword(candidates)
    return OcrResult(raw_text=raw_text, candidate_terms=candidates, selected_keyword=selected)


def _suggest_label(keyword: str) -> str:
    if not keyword:
        return ""
    return keyword[:32]


def run_image_search(
    conn: sqlite3.Connection,
    ctx: CommandContext,
    client,
    engine: OcrEngine | None = None,
) -> None:
    if not ctx.replied_photo_file_id:
        client.send_message(
            "❌ Responda a uma foto enviada ao bot com /imagesearch para buscar via OCR."
        )
        return

    image_bytes = client.download_file(ctx.replied_photo_file_id)
    if not image_bytes:
        client.send_message("❌ Não consegui baixar a imagem do Telegram.")
        return

    result = run_ocr_pipeline(image_bytes, engine=engine)
    if not result.selected_keyword:
        client.send_message("❌ OCR não encontrou texto utilizável na imagem.")
        return

    info_logger.info(f"/imagesearch OCR keyword: {result.selected_keyword}")
    client.send_message(
        f"🔎 OCR busca: <b>{escape(result.selected_keyword)}</b>"
    )

    rate_service = ExchangeRateService()
    rate = rate_service.get_exchange_rate_with_fallback()
    translator = TranslationService()
    session = create_buyee_session()

    items = fetch_items_one_off(
        result.selected_keyword,
        rate,
        translator,
        session=session,
        conn=conn,
        limit=20,
    )
    if not items:
        client.send_message("ℹ️ Nenhum resultado para essa busca por imagem.")
        return

    for item in items:
        client.send_photo(
            item["title"],
            item["url"],
            item["image_url"],
            item["price"],
            keyword_label="OCR search",
        )
        time.sleep(0.5)

    label = _suggest_label(result.selected_keyword)
    client.send_message(
        "💡 Para salvar essa busca, use:\n"
        f"<code>/addkeyword {escape(result.selected_keyword)} = {escape(label)}</code>"
    )
