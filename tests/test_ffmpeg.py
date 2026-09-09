from __future__ import annotations

import os

import pytest

from app.core.ffmpeg import FfmpegTools, detect, install_hint


def test_detect_never_raises_and_reports_a_source():
    tools = detect()
    assert isinstance(tools, FfmpegTools)
    assert tools.source in ("config", "path", "bundled", "none")
    if tools.available:
        assert os.path.isfile(tools.ffmpeg)
        assert tools.ffmpeg_version


def test_bogus_configured_path_falls_back():
    tools = detect(configured_ffmpeg="/definitely/not/here/ffmpeg")
    # falls back to PATH / bundled / none - must not crash, must not use the bogus path
    assert tools.ffmpeg != "/definitely/not/here/ffmpeg"


def test_install_hint_is_nonempty():
    assert "FFmpeg" in install_hint() or "ffmpeg" in install_hint()


def test_configured_file_path_is_used(ffmpeg_tools):
    tools = detect(configured_ffmpeg=ffmpeg_tools.ffmpeg)
    assert tools.available
    assert tools.ffmpeg == ffmpeg_tools.ffmpeg
    assert tools.source == "config"


def test_configured_directory_is_resolved(ffmpeg_tools):
    # given a directory, detect() must find the ffmpeg executable inside it.
    # Use the real ffmpeg's own directory: copying the binary elsewhere breaks
    # relocatable launchers (e.g. the Chocolatey shim on the Windows runner).
    directory, name = os.path.split(ffmpeg_tools.ffmpeg)
    if name not in ("ffmpeg", "ffmpeg.exe"):
        pytest.skip(f"detected ffmpeg has a non-standard filename ({name!r})")
    tools = detect(configured_ffmpeg=directory)
    assert tools.available
    assert tools.source == "config"
    assert os.path.dirname(tools.ffmpeg) == directory
