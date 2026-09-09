"""Cross-platform locations for config, cache, data and logs.

Follows the platform conventions:
  * Windows : %APPDATA% / %LOCALAPPDATA%
  * macOS   : ~/Library/Application Support, ~/Library/Caches
  * Linux   : XDG base directories (~/.config, ~/.cache, ~/.local/share)
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

from app import ORG_NAME

_SYSTEM = platform.system()


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def config_dir() -> Path:
    if _SYSTEM == "Windows":
        base = os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming")
    elif _SYSTEM == "Darwin":
        base = _home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config")
    path = Path(base) / ORG_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def data_dir() -> Path:
    if _SYSTEM == "Windows":
        base = os.environ.get("LOCALAPPDATA") or (_home() / "AppData" / "Local")
    elif _SYSTEM == "Darwin":
        base = _home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME") or (_home() / ".local" / "share")
    path = Path(base) / ORG_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    if _SYSTEM == "Windows":
        base = os.environ.get("LOCALAPPDATA") or (_home() / "AppData" / "Local")
    elif _SYSTEM == "Darwin":
        base = _home() / "Library" / "Caches"
    else:
        base = os.environ.get("XDG_CACHE_HOME") or (_home() / ".cache")
    path = Path(base) / ORG_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def thumbnail_cache_dir() -> Path:
    path = cache_dir() / "thumbnails"
    path.mkdir(parents=True, exist_ok=True)
    return path
