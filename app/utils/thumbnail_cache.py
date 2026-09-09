"""Thumbnail generation with a two-level cache (memory + disk).

* Full-resolution images are never held in memory - only downscaled PNG
  thumbnails.
* EXIF orientation is normalised for display (via ``ImageOps.exif_transpose``)
  but the source file is never touched.
* The disk cache key includes the file's path, size and mtime, so a changed
  file transparently regenerates its thumbnail.
"""
from __future__ import annotations

import hashlib
import io
import os
import threading
from collections import OrderedDict
from typing import Optional

from PIL import Image, ImageOps

from app.core.metadata import read_image_info  # noqa: F401  (keeps plugin registration side-effect)
from app.utils.logging_setup import get_logger
from app.utils.paths import thumbnail_cache_dir

log = get_logger(__name__)

_VIDEO_TOOLS = None


def _video_tools():
    """Lazily auto-detect FFmpeg for video thumbnails (config-independent)."""
    global _VIDEO_TOOLS
    if _VIDEO_TOOLS is None:
        from app.core.ffmpeg import detect

        _VIDEO_TOOLS = detect()
    return _VIDEO_TOOLS


class ThumbnailCache:
    def __init__(self, max_edge: int = 320, mem_items: int = 400) -> None:
        self.max_edge = max_edge
        self._mem: "OrderedDict[str, bytes]" = OrderedDict()
        self._mem_items = mem_items
        self._lock = threading.Lock()
        self._dir = thumbnail_cache_dir()

    # ------------------------------------------------------------------
    def _key(self, path: str, size: int, mtime: float) -> str:
        raw = f"{os.path.abspath(path)}|{size}|{int(mtime)}|{self.max_edge}"
        return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()

    def _disk_path(self, key: str) -> str:
        sub = self._dir / key[:2]
        sub.mkdir(parents=True, exist_ok=True)
        return str(sub / f"{key}.png")

    # ------------------------------------------------------------------
    def get_png(self, path: str, size: int, mtime: float) -> Optional[bytes]:
        """Return PNG bytes for a thumbnail, or ``None`` if the image is
        unreadable. Safe to call from any thread."""
        key = self._key(path, size, mtime)

        with self._lock:
            cached = self._mem.get(key)
            if cached is not None:
                self._mem.move_to_end(key)
                return cached

        disk = self._disk_path(key)
        if os.path.exists(disk):
            try:
                with open(disk, "rb") as fh:
                    data = fh.read()
                self._store_mem(key, data)
                return data
            except OSError:
                pass

        data = self._render(path)
        if data is None:
            return None
        try:
            tmp = disk + f".{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "wb") as fh:
                fh.write(data)
            os.replace(tmp, disk)
        except OSError:
            pass
        self._store_mem(key, data)
        return data

    # ------------------------------------------------------------------
    def _store_mem(self, key: str, data: bytes) -> None:
        with self._lock:
            self._mem[key] = data
            self._mem.move_to_end(key)
            while len(self._mem) > self._mem_items:
                self._mem.popitem(last=False)

    def _render(self, path: str) -> Optional[bytes]:
        import os

        from app.core.scanner import VIDEO_EXTENSIONS

        if os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS:
            return self._render_video_frame(path)
        try:
            with Image.open(path) as img:
                img = ImageOps.exif_transpose(img)
                img.thumbnail((self.max_edge, self.max_edge), Image.LANCZOS)
                if img.mode not in ("RGB", "RGBA", "L", "LA"):
                    img = img.convert("RGBA")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
        except Exception as exc:  # noqa: BLE001 - corrupt/unknown files
            log.debug("Thumbnail failed for %s: %s", path, exc)
            return None

    def _render_video_frame(self, path: str) -> Optional[bytes]:
        tools = _video_tools()
        if not tools.available:
            return None
        from app.core.video_analyzer import _grab_frame_png

        png = _grab_frame_png(path, tools.ffmpeg, 1.0) or _grab_frame_png(
            path, tools.ffmpeg, 0.0
        )
        if not png:
            return None
        try:
            with Image.open(io.BytesIO(png)) as img:
                img = img.convert("RGB")
                img.thumbnail((self.max_edge, self.max_edge), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()
        except Exception as exc:  # noqa: BLE001
            log.debug("Video thumbnail failed for %s: %s", path, exc)
            return None

    def clear_memory(self) -> None:
        with self._lock:
            self._mem.clear()
