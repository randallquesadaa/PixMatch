"""Locate FFmpeg / ffprobe.

Order of preference:
  1. a path the user configured in Settings
  2. ``ffmpeg`` / ``ffprobe`` on the system PATH
  3. the binary bundled by the optional ``imageio-ffmpeg`` package (ffmpeg only)

If nothing is found, video analysis is disabled and the user is told how to
install FFmpeg. Nothing here ever modifies a media file.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass

from app.utils.logging_setup import get_logger

log = get_logger(__name__)

_SYSTEM = platform.system()


@dataclass
class FfmpegTools:
    ffmpeg: str | None = None
    ffprobe: str | None = None
    ffmpeg_version: str | None = None
    source: str = "none"  # config | path | bundled | none

    @property
    def available(self) -> bool:
        return self.ffmpeg is not None

    @property
    def has_ffprobe(self) -> bool:
        return self.ffprobe is not None


def install_hint() -> str:
    if _SYSTEM == "Windows":
        return (
            "Instala FFmpeg: `winget install Gyan.FFmpeg` o descárgalo de "
            "https://www.gyan.dev/ffmpeg/builds/ y añádelo al PATH. "
            "Alternativa rápida: `pip install imageio-ffmpeg`."
        )
    if _SYSTEM == "Darwin":
        return (
            "Instala FFmpeg con Homebrew: `brew install ffmpeg`. "
            "Alternativa rápida: `pip install imageio-ffmpeg`."
        )
    return (
        "Instala FFmpeg con tu gestor de paquetes, p. ej. "
        "`sudo apt install ffmpeg` o `sudo dnf install ffmpeg`. "
        "Alternativa rápida: `pip install imageio-ffmpeg`."
    )


def _exe_names(base: str) -> list[str]:
    return [base + ".exe", base] if _SYSTEM == "Windows" else [base]


def _resolve(configured: str, base: str) -> str | None:
    """Turn a user setting (file or directory) into an executable path."""
    configured = configured.strip()
    if not configured:
        return None
    if os.path.isdir(configured):
        for name in _exe_names(base):
            candidate = os.path.join(configured, name)
            if os.path.isfile(candidate):
                return candidate
        return None
    return configured if os.path.isfile(configured) else None


def _probe_version(path: str) -> str | None:
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("ffmpeg probe failed for %s: %s", path, exc)
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.splitlines()[0].strip()


def _no_window() -> int:
    # avoid a flashing console window on Windows
    if _SYSTEM == "Windows":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _bundled_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        return exe if exe and os.path.isfile(exe) else None
    except Exception:
        return None


def detect(configured_ffmpeg: str = "", configured_ffprobe: str = "") -> FfmpegTools:
    tools = FfmpegTools()

    # --- ffmpeg ---
    from_config = _resolve(configured_ffmpeg, "ffmpeg")
    if from_config and _probe_version(from_config):
        tools.ffmpeg = from_config
        tools.source = "config"
    if tools.ffmpeg is None:
        on_path = shutil.which("ffmpeg")
        if on_path and _probe_version(on_path):
            tools.ffmpeg, tools.source = on_path, "path"
    if tools.ffmpeg is None:
        bundled = _bundled_ffmpeg()
        if bundled and _probe_version(bundled):
            tools.ffmpeg, tools.source = bundled, "bundled"
    if tools.ffmpeg:
        tools.ffmpeg_version = _probe_version(tools.ffmpeg)

    # --- ffprobe ---
    probe_config = _resolve(configured_ffprobe, "ffprobe")
    if probe_config and _probe_version(probe_config):
        tools.ffprobe = probe_config
    if tools.ffprobe is None and tools.ffmpeg:
        # try a sibling of the chosen ffmpeg first
        sibling = os.path.join(os.path.dirname(tools.ffmpeg), _exe_names("ffprobe")[0])
        if os.path.isfile(sibling) and _probe_version(sibling):
            tools.ffprobe = sibling
    if tools.ffprobe is None:
        on_path = shutil.which("ffprobe")
        if on_path and _probe_version(on_path):
            tools.ffprobe = on_path

    log.info(
        "FFmpeg detection: ffmpeg=%s (%s) ffprobe=%s",
        tools.ffmpeg,
        tools.source,
        tools.ffprobe,
    )
    return tools
