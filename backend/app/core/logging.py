import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from backend.app.core.config import RUNTIME_DIR


LOG_DIR = RUNTIME_DIR / "logs"
APP_LOG_PATH = LOG_DIR / "app.log"
ERROR_LOG_PATH = LOG_DIR / "error.log"


def configure_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    max_bytes = int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
    backup_count = int(os.getenv("LOG_BACKUP_COUNT", "5"))
    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(log_format)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if root_logger.handlers:
        root_logger.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        APP_LOG_PATH,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    error_handler = RotatingFileHandler(
        ERROR_LOG_PATH,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    root_logger.addHandler(error_handler)
