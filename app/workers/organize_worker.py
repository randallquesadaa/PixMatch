"""Background workers for the "Organizar biblioteca" tool.

Same pattern as :mod:`app.workers.import_worker`: a plain ``threading.Thread``
does the work, the QObject stays on the GUI thread and its signals reach the
UI through queued connections.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from app.config import AppConfig
from app.core.geo import CountryResolver
from app.core.organizer import (
    OrganizeReport,
    apply_organize,
    build_organize_plan,
    undo_organize,
)
from app.core.scanner import FileKind, Scanner, ScanOptions
from app.database.history import OperationHistory
from app.utils.control import RunController
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


class OrganizePreviewRunner(QObject):
    """Scan the root folder, then build the reorganise plan."""

    progress = Signal(str)
    finished = Signal(object)  # list[OrganizePlan]
    failed = Signal(str)

    def __init__(self, root: str, config: AppConfig) -> None:
        super().__init__(None)
        self._root = root
        self._config = config
        self.controller = RunController()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="organize-preview")
        self._thread.start()

    def cancel(self) -> None:
        self.controller.cancel()

    def wait(self, ms: int = 15000) -> None:
        if self._thread is not None:
            self._thread.join(ms / 1000.0)

    def _run(self) -> None:
        try:
            cfg = self._config
            kinds = {FileKind.IMAGE}
            if cfg.import_include_videos:
                kinds.add(FileKind.VIDEO)
            opts = ScanOptions(
                follow_symlinks=cfg.follow_symlinks,
                excluded_dir_names=frozenset(cfg.excluded_dir_names),
                include_kinds=frozenset(kinds),
            )

            self.progress.emit("Escaneando la carpeta…")
            source_files: list = []
            Scanner(self._root, opts, self.controller).scan(
                on_file=source_files.append,
                on_progress=lambda s: self.progress.emit(
                    f"Escaneando la carpeta… {s.files_found:,} archivos"
                ),
            )
            if self.controller.is_cancelled:
                self.finished.emit([])
                return
            if not source_files:
                self.failed.emit("No se encontraron imágenes ni vídeos en esa carpeta.")
                return

            video_infos: dict = {}
            if cfg.import_include_videos and any(f.kind is FileKind.VIDEO for f in source_files):
                try:
                    from app.core.ffmpeg import detect
                    from app.core.video_analyzer import probe_video

                    tools = detect(cfg.ffmpeg_path, cfg.ffprobe_path)
                    if tools.available:
                        vids = [f for f in source_files if f.kind is FileKind.VIDEO]
                        for n, f in enumerate(vids, 1):
                            if not self.controller.checkpoint():
                                break
                            self.progress.emit(f"Leyendo metadatos de vídeo… {n}/{len(vids)}")
                            video_infos[f.path] = probe_video(f.path, tools)
                except Exception as exc:
                    log.warning("Video probe during organize failed: %s", exc)

            self.progress.emit(f"Preparando {len(source_files):,} archivos…")
            resolver = CountryResolver()
            plans = build_organize_plan(
                source_files,
                root=self._root,
                resolver=resolver,
                config=cfg,
                video_infos=video_infos,
                check=self.controller.checkpoint,
                on_progress=lambda done, total: self.progress.emit(
                    f"Preparando… {done:,} / {total:,}"
                ),
            )
            self.finished.emit(plans)
        except Exception as exc:
            log.exception("Organize preview crashed")
            self.failed.emit(str(exc))


class OrganizeApplyRunner(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)  # OrganizeReport
    failed = Signal(str)

    def __init__(self, plans, history: OperationHistory, *, undo_of=None) -> None:
        super().__init__(None)
        self._plans = plans
        self._history_path = history.path
        self._undo_of = undo_of
        self.controller = RunController()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="organize-apply")
        self._thread.start()

    def cancel(self) -> None:
        self.controller.cancel()

    def wait(self, ms: int = 30000) -> None:
        if self._thread is not None:
            self._thread.join(ms / 1000.0)

    def _run(self) -> None:
        history = OperationHistory(self._history_path)
        try:
            if self._undo_of is not None:
                report: OrganizeReport = undo_organize(
                    self._undo_of, history=history, check=self.controller.checkpoint
                )
            else:
                report = apply_organize(
                    self._plans,
                    history=history,
                    progress=lambda d, t, n: self.progress.emit(d, t, n),
                    check=self.controller.checkpoint,
                )
            self.finished.emit(report)
        except Exception as exc:
            log.exception("Organize apply crashed")
            self.failed.emit(str(exc))
        finally:
            history.close()
