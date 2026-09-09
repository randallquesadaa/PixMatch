"""Filesystem helpers: human-readable sizes, long-path handling on Windows,
and opening files / folders in the OS."""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

_SYSTEM = platform.system()


def human_size(num_bytes: float) -> str:
    """1234567 -> '1.18 MB'. Uses binary units."""
    if num_bytes < 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PB"


def human_duration(seconds: float) -> str:
    """Seconds -> 'HH:MM:SS'."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def extended_path(path: str | os.PathLike[str]) -> str:
    r"""On Windows, prefix with \\?\ so paths longer than 260 chars work with
    the file APIs. No-op elsewhere."""
    p = os.fspath(path)
    if _SYSTEM != "Windows":
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):  # UNC path
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def open_in_file_manager(path: str | os.PathLike[str]) -> None:
    """Open the OS file manager with *path* selected (or the folder itself)."""
    p = Path(path)
    try:
        if _SYSTEM == "Windows":
            if p.is_dir():
                os.startfile(str(p))  # type: ignore[attr-defined]
            else:
                subprocess.run(["explorer", "/select,", str(p)], check=False)
        elif _SYSTEM == "Darwin":
            if p.is_dir():
                subprocess.run(["open", str(p)], check=False)
            else:
                subprocess.run(["open", "-R", str(p)], check=False)
        else:
            target = str(p if p.is_dir() else p.parent)
            subprocess.run(["xdg-open", target], check=False)
    except Exception:  # pragma: no cover - best effort, never crash the UI
        pass


def open_with_default_app(path: str | os.PathLike[str]) -> None:
    p = str(path)
    try:
        if _SYSTEM == "Windows":
            os.startfile(p)  # type: ignore[attr-defined]
        elif _SYSTEM == "Darwin":
            subprocess.run(["open", p], check=False)
        else:
            subprocess.run(["xdg-open", p], check=False)
    except Exception:  # pragma: no cover
        pass


def is_hidden(path: Path) -> bool:
    name = path.name
    if name.startswith("."):
        return True
    if _SYSTEM == "Windows":
        try:
            import ctypes

            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))  # type: ignore[attr-defined]
            return attrs != -1 and bool(attrs & 0x2)
        except Exception:
            return False
    return False
