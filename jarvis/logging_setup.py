"""Logging to logs/jarvis.log with secrets redacted."""

import logging
import re
from logging.handlers import RotatingFileHandler

from .config import PROJECT_ROOT

# Things that look like credentials even when we do not know their value.
SECRET_PATTERNS = [
    re.compile(r"sk-or-v1-[A-Za-z0-9]{10,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"sb_(secret|publishable)_[A-Za-z0-9_-]{10,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]


class RedactSecrets(logging.Filter):
    def __init__(self, secrets=()):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def redact(self, text):
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        for pattern in SECRET_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text

    def filter(self, record):
        record.msg = self.redact(record.getMessage())
        record.args = ()
        return True


def setup_logging(config):
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)

    handler = RotatingFileHandler(
        log_dir / "jarvis.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    ))
    handler.addFilter(RedactSecrets(config.secret_values()))

    root = logging.getLogger()
    root.setLevel(config.log_level)
    root.handlers = [handler]

    # Third-party HTTP libraries are noisy and may log request URLs.
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "hpack", "urllib3", "comtypes", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
