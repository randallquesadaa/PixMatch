"""Background workers for the rename-by-date tool.

Both run on a plain ``threading.Thread``; the QObject stays on the GUI thread
and its signals reach the UI through queued connections (same pattern as
:class:`app.workers.deletion_worker.DeletionRunner`).
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from app.config import AppConfig
from app.core.renamer import RenameReport, apply_renames, build_rename_plan, undo_renames
from app.core.scanner import FileKind, ScanOptions, Scanner
from app.database.history import OperationHistory
from app.utils.control import RunController
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


class RenamePreviewRunner(QObject):
    """Scan a folder and build the rename plan (reads EXIF - can be slow)."""

    progress = Signal(str)              # human status line
    finished = Signal(object)           # list[RenamePlan]
    failed = Signal(str)

    def __init__(self, folder: str, config: AppConfig) -> None:
        super().__init__(None)
        self._folder = folder
        self._config = config
        self.controller = RunController()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="rename-preview")
        self._thread.start()

    def cancel(self) -> None:
        self.controller.cancel()

    def wait(self, ms: int = 10000) -> None:
        if self._thread is not None:
            self._thread.join(ms / 1000.0)

    def _run(self) -> None:
        try:
            cfg = self._config
            kinds = {FileKind.IMAGE}
            if cfg.rename_include_videos:
                kinds.add(FileKind.VIDEO)
            opts = ScanOptions(
                follow_symlinks=cfg.follow_symlinks,
                excluded_dir_names=frozenset(cfg.excluded_dir_names),
                excluded_paths=frozenset(cfg.excluded_paths),
                include_kinds=frozenset(kinds),
            )
            collected: list = []
            Scanner(self._folder, opts, self.controller).scan(
                on_file=collected.append,
                on_progress=lambda s: self.progress.emit(
                    f"Escaneando… {s.files_found:,} archivos"
                ),
            )
            if self.controller.is_cancelled:
                self.finished.emit([])
                return

            tools = None
            if cfg.rename_include_videos and any(f.kind is FileKind.VIDEO for f in collected):
                try:
                    from app.core.ffmpeg import detect

                    tools = detect(cfg.ffmpeg_path, cfg.ffprobe_path)
                except Exception:  # noqa: BLE001
                    tools = None

            self.progress.emit(f"Leyendo fechas de {len(collected):,} archivos…")
            plans = build_rename_plan(
                collected,
                pattern=cfg.rename_pattern,
                prefix=cfg.rename_prefix,
                lowercase_ext=cfg.rename_lowercase_ext,
                normalise_jpeg=cfg.rename_normalise_jpeg,
                ffmpeg_tools=tools,
                check=self.controller.checkpoint,
                on_progress=lambda done, total: self.progress.emit(
                    f"Leyendo fechas… {done:,} / {total:,}"
                ),
            )
            self.finished.emit(plans)
        except Exception as exc:  # noqa: BLE001
            log.exception("Rename preview crashed")
            self.failed.emit(str(exc))
        finally:
            pass


class RenameApplyRunner(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)           # RenameReport
    failed = Signal(str)

    def __init__(self, plans, history: OperationHistory, *, undo_of=None) -> None:
        super().__init__(None)
        self._plans = plans
        self._history_path = history.path
        self._undo_of = undo_of
        self.controller = RunController()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="rename-apply")
        self._thread.start()

    def cancel(self) -> None:
        self.controller.cancel()

    def wait(self, ms: int = 15000) -> None:
        if self._thread is not None:
            self._thread.join(ms / 1000.0)

    def _run(self) -> None:
        history = OperationHistory(self._history_path)
        try:
            if self._undo_of is not None:
                report: RenameReport = undo_renames(
                    self._undo_of, history=history, check=self.controller.checkpoint
                )
            else:
                report = apply_renames(
                    self._plans, history=history,
                    progress=lambda d, t, n: self.progress.emit(d, t, n),
                    check=self.controller.checkpoint,
                )
            self.finished.emit(report)
        except Exception as exc:  # noqa: BLE001
            log.exception("Rename apply crashed")
            self.failed.emit(str(exc))
        finally:
            history.close()
