"""Off-thread thumbnail loading for the UI.

A :class:`QRunnable` renders (or fetches from cache) the PNG bytes on a pool
thread and hands them back to the GUI thread via a signal, where they become a
``QPixmap``. The GUI thread never decodes a full-resolution image.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QPixmap

from app.utils.thumbnail_cache import ThumbnailCache

# One process-wide cache and pool.
THUMBNAIL_CACHE = ThumbnailCache()
_POOL = QThreadPool.globalInstance()
_POOL.setMaxThreadCount(max(2, min(6, _POOL.maxThreadCount())))


class _Signals(QObject):
    done = Signal(str, object)   # path, QPixmap or None


class _ThumbTask(QRunnable):
    def __init__(self, path: str, size: int, mtime: float, edge: int) -> None:
        super().__init__()
        self.signals = _Signals()
        self._path = path
        self._size = size
        self._mtime = mtime
        self._edge = edge

    def run(self) -> None:
        png = THUMBNAIL_CACHE.get_png(self._path, self._size, self._mtime)
        pixmap: object = None
        if png:
            pm = QPixmap()
            if pm.loadFromData(png, "PNG"):
                if pm.width() > self._edge or pm.height() > self._edge:
                    pm = pm.scaled(
                        self._edge, self._edge,
                        Qt.KeepAspectRatio, Qt.SmoothTransformation,
                    )
                pixmap = pm
        try:
            self.signals.done.emit(self._path, pixmap)
        except RuntimeError:
            # Receiver or signal object was torn down (e.g. app shutting down
            # while a thumbnail was still rendering). Nothing to do.
            pass


def request_thumbnail(path: str, size: int, mtime: float, edge: int, slot) -> None:
    task = _ThumbTask(path, size, mtime, edge)
    task.signals.done.connect(slot, Qt.QueuedConnection)
    _POOL.start(task)
