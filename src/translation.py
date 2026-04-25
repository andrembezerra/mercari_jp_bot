import asyncio
import logging

try:
    from googletrans import Translator
except ModuleNotFoundError:
    class Translator:  # type: ignore[no-redef]
        async def translate(self, title, src="ja", dest="en"):
            class _TranslationResult:
                def __init__(self, text):
                    self.text = text

            return _TranslationResult(title)


class TranslationService:
    def __init__(self) -> None:
        self.translator = Translator()
        self._loop = None

    def get_loop(self):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        return self._loop

    def translate_title_with_fallback(self, title: str) -> str:
        try:
            translated = self.get_loop().run_until_complete(
                self.translator.translate(title, src="ja", dest="en")
            )
            if translated and hasattr(translated, "text"):
                title_en = translated.text
                if title_en and title_en.strip() and title_en != title:
                    return f"{title_en} ({title})"
            return title
        except Exception as exc:
            logging.warning(f"Translation failed for title: {title[:50]}... | Error: {exc}")
            return title

