import logging
import sys


def configure_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    info_logger = logging.getLogger("info")
    info_logger.setLevel(logging.INFO)

    if not info_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
        info_logger.addHandler(handler)

    info_logger.propagate = False
    return info_logger


info_logger = configure_logging()

