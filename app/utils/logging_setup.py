"""Application-wide logging.

Technical errors go to a rotating log file (viewable from
Settings -> Logs), not to the user interface.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from app.utils.paths import logs_dir

_CONFIGURED = False
LOG_FILE = logs_dir() / "pixmatch.log"


def configure_logging(level: int = logging.INFO) -> Path:
    """Set up the root logger once. Returns the path to the log file."""
    global _CONFIGURED
    if _CONFIGURED:
        return LOG_FILE

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    _CONFIGURED = True
    logging.getLogger(__name__).info("Logging initialised -> %s", LOG_FILE)
    return LOG_FILE


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
