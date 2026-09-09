"""A small, thread-safe cooperative pause / resume / cancel primitive.

Long-running work (scanning, hashing) calls :meth:`RunController.checkpoint`
frequently. That call blocks while the run is paused and returns ``False``
once the run has been cancelled, so callers can stop cleanly without any
forced thread termination.
"""

from __future__ import annotations

import threading


class Cancelled(Exception):
    """Raised by helpers that prefer to abort with an exception."""


class RunController:
    def __init__(self) -> None:
        self._cancelled = threading.Event()
        # _resumed is set when NOT paused (so waiters can block on it cheaply).
        self._resumed = threading.Event()
        self._resumed.set()

    # -- state changes (called from the UI thread) ------------------------
    def pause(self) -> None:
        if not self._cancelled.is_set():
            self._resumed.clear()

    def resume(self) -> None:
        self._resumed.set()

    def cancel(self) -> None:
        self._cancelled.set()
        self._resumed.set()  # wake any paused worker so it can exit

    # -- queries ---------------------------------------------------------
    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def is_paused(self) -> bool:
        return not self._resumed.is_set() and not self._cancelled.is_set()

    # -- called from worker threads ------------------------------------
    def checkpoint(self, timeout: float | None = None) -> bool:
        """Block while paused. Return ``False`` if the run was cancelled."""
        # Wait in short slices so a cancel during a long pause is responsive.
        while not self._resumed.wait(0.1):
            if self._cancelled.is_set():
                return False
        return not self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if not self.checkpoint():
            raise Cancelled()
