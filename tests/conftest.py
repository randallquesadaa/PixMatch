"""Shared fixtures. All test data is generated into pytest's tmp_path and is
therefore temporary and isolated."""
from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def make_image():
    """Return a factory: make_image(path, size=(w,h), color=..., fmt=..., **save_kw)."""

    def _factory(path, size=(64, 48), color=(200, 30, 30), fmt=None, **save_kw):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", size, color)
        # a little structure so perceptual tests later have something to chew on
        for x in range(0, size[0], 8):
            for y in range(0, size[1], 8):
                img.putpixel((x, y), (0, 0, 0))
        fmt = fmt or ("PNG" if path.suffix.lower() == ".png" else "JPEG")
        img.save(path, format=fmt, **save_kw)
        return path

    return _factory


@pytest.fixture
def raw_image_bytes():
    def _bytes(size=(32, 32), color=(10, 120, 240), fmt="PNG"):
        buf = io.BytesIO()
        Image.new("RGB", size, color).save(buf, format=fmt)
        return buf.getvalue()

    return _bytes


@pytest.fixture(scope="session")
def ffmpeg_tools():
    """The detected FFmpeg tools, or skip the test if none is available."""
    from app.core.ffmpeg import detect

    tools = detect()
    if not tools.available:
        pytest.skip("FFmpeg no disponible en el entorno de pruebas")
    return tools


@pytest.fixture
def make_video(ffmpeg_tools):
    """make_video(path, pattern='testsrc', duration=2, size=(160,120), rate=10, crf=23)."""
    import subprocess

    def _factory(path, pattern="testsrc", duration=2, size=(160, 120), rate=10, crf=23):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        src = f"{pattern}=duration={duration}:size={size[0]}x{size[1]}:rate={rate}"
        cmd = [
            ffmpeg_tools.ffmpeg, "-nostdin", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", src,
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", str(crf),
            "-c:a", "aac", "-shortest", str(path),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        return path

    return _factory
