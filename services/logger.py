from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
LOG_FILE = LOG_DIR / "bot.log"
LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | "
    "%(filename)s:%(lineno)d | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_SECRET_NAMES = (
    "BOT_TOKEN",
    "MONGO_URI",
    "SECRET_KEY",
    "DASHBOARD_PASSWORD",
    "RAZORPAY_KEY_SECRET",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "CASHFREE_SECRET_KEY",
    "PAYU_SALT",
    "PAYTM_MERCHANT_KEY",
)

_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
_MONGO_CREDENTIAL_RE = re.compile(r"(mongodb(?:\+srv)?://[^:\s/]+:)([^@\s]+)(@)")
_KEY_VALUE_RE = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key|authorization)\b"
    r"(\s*[:=]\s*)([^\s,;]+)"
)


def mask_sensitive(value: Any) -> str:
    """Return text safe enough for application logs."""
    text = str(value)

    for name in _SECRET_NAMES:
        secret = os.getenv(name, "").strip()
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***REDACTED***")

    text = _TELEGRAM_TOKEN_RE.sub("***TELEGRAM_TOKEN***", text)
    text = _MONGO_CREDENTIAL_RE.sub(r"\1***REDACTED***\3", text)
    text = _KEY_VALUE_RE.sub(r"\1\2***REDACTED***", text)
    return text


class SensitiveDataFilter(logging.Filter):
    """Mask credentials in both log messages and exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = mask_sensitive(record.getMessage())
            record.args = ()
        except Exception:
            # Logging must never crash the application.
            pass
        return True


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
    sensitive_filter = SensitiveDataFilter()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sensitive_filter)
    root_logger.addHandler(console_handler)

    try:
        file_handler = RotatingFileHandler(
            LOG_FILE,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(sensitive_filter)
        root_logger.addHandler(file_handler)
    except OSError:
        root_logger.warning(
            "File logging could not be initialized; console logging remains active.",
            exc_info=True,
        )

    root_logger.info("Logging initialized successfully level=%s", level_name)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
