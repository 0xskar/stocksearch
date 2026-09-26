"""Shared logging setup for both the CLI (research.py) and the web UI (app.py)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(log_path: str, level: str = "INFO") -> None:
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if root.handlers:
        # Idempotent: guards against duplicate handlers if this is called more
        # than once in the same process (e.g. tests importing multiple modules).
        return

    root.setLevel(level.upper())
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    # Third-party libraries that log at INFO by default and would otherwise
    # drown out our own concise progress lines (e.g. ddgs's HTTP client logs
    # every request at INFO).
    logging.getLogger("primp").setLevel(logging.WARNING)
