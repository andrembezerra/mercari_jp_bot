import logging
import re
import time

import requests

from src.logging_setup import info_logger


class ExchangeRateService:
    def __init__(self, cache_duration: int = 3600) -> None:
        self.cache_duration = cache_duration
        self.cached_exchange_rate = None
        self.last_exchange_rate_update = None

    def get_usd_to_jpy_rate(self) -> float:
        try:
            response = requests.get("https://open.er-api.com/v6/latest/USD", timeout=5)
            response.raise_for_status()
            data = response.json()
            rate = float(data["rates"]["JPY"])
            logging.info(f"Fetched USD to JPY exchange rate: {rate}")
            return rate
        except requests.exceptions.RequestException as exc:
            logging.warning(
                f"⚠️ Failed to fetch exchange rate, using fallback (145.0). Error: {exc}"
            )
            return 145.0
        except (KeyError, TypeError) as exc:
            logging.warning(
                f"⚠️ Failed to parse exchange rate data, using fallback (145.0). Error: {exc}"
            )
            return 145.0

    def get_exchange_rate_with_fallback(self) -> float:
        current_time = time.time()
        if (
            self.cached_exchange_rate is not None
            and self.last_exchange_rate_update is not None
            and current_time - self.last_exchange_rate_update < self.cache_duration
        ):
            logging.debug(f"Using cached exchange rate: {self.cached_exchange_rate}")
            return self.cached_exchange_rate

        try:
            rate = self.get_usd_to_jpy_rate()
            self.cached_exchange_rate = rate
            self.last_exchange_rate_update = current_time
            info_logger.info(f"✅ Updated exchange rate: {rate}")
            return rate
        except Exception as exc:
            logging.warning(
                f"⚠️ Exchange rate fetch failed, using cached/default rate. Error: {exc}"
            )
            if self.cached_exchange_rate is not None:
                info_logger.info(f"Using cached exchange rate: {self.cached_exchange_rate}")
                return self.cached_exchange_rate
            info_logger.info("Using default exchange rate: 145.0")
            return 145.0


def convert_price_to_yen(text: str, rate: float) -> tuple[str | None, int | None]:
    yen_match = re.search(r"([\d,]+)\s*yen", text, re.IGNORECASE)
    if yen_match:
        amount_str = yen_match.group(1)
        try:
            amount_int = int(amount_str.replace(",", ""))
            return f"¥{amount_int:,}".replace(",", "."), amount_int
        except ValueError:
            logging.warning(f"Could not parse yen amount '{amount_str}' from text: {text}")
            return None, None

    match = re.search(r"(¥|US\$|\$)\s*([\d,]+)", text)
    if not match:
        logging.debug(f"No price found in text: {text}")
        return None, None

    symbol, amount_str = match.groups()
    try:
        amount_int = int(amount_str.replace(",", ""))
    except ValueError:
        logging.warning(f"Could not parse amount '{amount_str}' from text: {text}")
        return None, None

    if symbol in ["US$", "$"]:
        yen = int(amount_int * rate)
    elif symbol == "¥":
        yen = amount_int
    else:
        logging.warning(f"Unknown currency symbol '{symbol}' in text: {text}")
        return None, None

    return f"¥{yen:,}".replace(",", "."), yen
