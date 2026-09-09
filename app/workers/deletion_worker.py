"""Qt adapter for :class:`app.core.deletion_manager.DeletionManager`.

Deletion runs on a plain background thread (a slow network share or a large
batch must not freeze the UI) and is cooperatively cancellable. The runner
object itself stays on the GUI thread; Qt signals emitted from the worker
thread are delivered to GUI slots via queued connections.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from app.core.deletion_manager import (
    DeletionManager,
    DeletionMode,
    DeletionReport,
    PlannedDeletion,
)
from app.database.history import OperationHistory
from app.utils.control import RunController
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


class DeletionRunner(QObject):
    progress = Signal(int, int, str)      # done, total, current path
    finished = Signal(object)             # DeletionReport
    failed = Signal(str)

    def __init__(
        self,
        planned: list[PlannedDeletion],
        mode: DeletionMode,
        history: OperationHistory,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._planned = planned
        self._mode = mode
        # Use the history file path and open a fresh connection on the worker
        # thread, so no SQLite connection is shared across threads.
        self._history_path = history.path
        self.controller = RunController()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="deletion")
        self._thread.start()

    def cancel(self) -> None:
        self.controller.cancel()

    def wait(self, ms: int = 10000) -> None:
        if self._thread is not None:
            self._thread.join(ms / 1000.0)

    def _run(self) -> None:
        history = OperationHistory(self._history_path)
        try:
            manager = DeletionManager(history)
            report: DeletionReport = manager.delete(
                self._planned,
                mode=self._mode,
                progress=lambda d, t, p: self.progress.emit(d, t, p),
                check=self.controller.checkpoint,
            )
            self.finished.emit(report)
        except Exception as exc:  # noqa: BLE001
            log.exception("Deletion crashed")
            self.failed.emit(str(exc))
        finally:
            history.close()
