"""Qt adapter around :func:`app.core.analysis.run_analysis`.

The heavy work runs in a :class:`QThread`; the UI only ever touches this
object's signals. Pause / resume / cancel are delegated to a thread-safe
:class:`RunController`.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from app.config import AppConfig
from app.core.analysis import (
    AnalysisCallbacks,
    StepProgress,
    run_analysis,
)
from app.core.duplicate_groups import AnalysisResult
from app.core.scanner import ScanStats
from app.utils.control import RunController
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


class _SignalCallbacks(AnalysisCallbacks):
    def __init__(self, worker: AnalysisWorker) -> None:
        self._w = worker

    def on_phase(self, phase: str) -> None:
        self._w.phase_changed.emit(phase)

    def on_scan_progress(self, stats: ScanStats) -> None:
        self._w.scan_progress.emit(stats)

    def on_step_progress(self, progress: StepProgress) -> None:
        self._w.step_progress.emit(progress)

    def on_message(self, text: str) -> None:
        self._w.message.emit(text)


class AnalysisWorker(QObject):
    phase_changed = Signal(str)
    scan_progress = Signal(object)  # ScanStats
    step_progress = Signal(object)  # StepProgress
    message = Signal(str)
    finished = Signal(object)  # AnalysisResult
    failed = Signal(str)

    def __init__(self, root: str, config: AppConfig, incremental: bool = False) -> None:
        super().__init__()
        self._root = root
        self._config = config
        self._incremental = incremental
        self.controller = RunController()

    # -- controls (call from the UI thread) ----------------------------
    def pause(self) -> None:
        self.controller.pause()

    def resume(self) -> None:
        self.controller.resume()

    def cancel(self) -> None:
        self.controller.cancel()

    # -- entry point (runs in the worker thread) ---------------------
    def run(self) -> None:
        try:
            result: AnalysisResult = run_analysis(
                self._root,
                self._config,
                controller=self.controller,
                callbacks=_SignalCallbacks(self),
                incremental=self._incremental,
            )
            self.finished.emit(result)
        except Exception as exc:
            log.exception("Analysis crashed")
            self.failed.emit(str(exc))


class AnalysisController(QObject):
    """Owns the worker + its thread and re-exposes the signals.

    Keeps the QThread lifecycle in one place so the window doesn't have to.
    """

    phase_changed = Signal(str)
    scan_progress = Signal(object)
    step_progress = Signal(object)
    message = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: AnalysisWorker | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @property
    def paused(self) -> bool:
        return bool(self._worker and self._worker.controller.is_paused)

    def start(self, root: str, config: AppConfig, incremental: bool = False) -> None:
        if self.running:
            raise RuntimeError("Ya hay un análisis en curso.")

        thread = QThread()
        worker = AnalysisWorker(root, config, incremental)
        worker.moveToThread(thread)

        worker.phase_changed.connect(self.phase_changed)
        worker.scan_progress.connect(self.scan_progress)
        worker.step_progress.connect(self.step_progress)
        worker.message.connect(self.message)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)

        thread.started.connect(worker.run)
        self._thread, self._worker = thread, worker
        thread.start()

    def pause(self) -> None:
        if self._worker:
            self._worker.pause()

    def resume(self) -> None:
        if self._worker:
            self._worker.resume()

    def cancel(self) -> None:
        if self._worker:
            self._worker.cancel()

    # -- internals ---------------------------------------------------
    def _on_finished(self, result: object) -> None:
        self._teardown()
        self.finished.emit(result)
        self.stopped.emit()

    def _on_failed(self, msg: str) -> None:
        self._teardown()
        self.failed.emit(msg)
        self.stopped.emit()

    def _teardown(self) -> None:
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(5000)
        self._thread = None
        self._worker = None

    def shutdown(self) -> None:
        """Called on app close - stop cleanly."""
        if self._worker:
            self._worker.cancel()
        self._teardown()
