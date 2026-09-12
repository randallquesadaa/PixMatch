"""Background workers for the import / organise tool.

Same pattern as :mod:`app.workers.rename_worker`: a plain ``threading.Thread``
does the work, the QObject stays on the GUI thread and its signals reach the UI
through queued connections.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from app.config import AppConfig
from app.core.geo import CountryResolver
from app.core.importer import (
    ImportReport,
    LibraryIndex,
    apply_import,
    build_import_plan,
    is_within,
    undo_import,
)
from app.core.scanner import FileKind, Scanner, ScanOptions
from app.database.history import OperationHistory
from app.utils.control import RunController
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


class ImportPreviewRunner(QObject):
    """Scan the source + index the library, then build the import plan."""

    progress = Signal(str)
    finished = Signal(object)  # list[ImportPlan]
    failed = Signal(str)

    def __init__(self, source: str, library: str, config: AppConfig) -> None:
        super().__init__(None)
        self._source = source
        self._library = library
        self._config = config
        self.controller = RunController()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="import-preview")
        self._thread.start()

    def cancel(self) -> None:
        self.controller.cancel()

    def wait(self, ms: int = 15000) -> None:
        if self._thread is not None:
            self._thread.join(ms / 1000.0)

    def _run(self) -> None:
        try:
            cfg = self._config
            if is_within(self._source, self._library):
                self.failed.emit(
                    "La carpeta de origen está dentro de la biblioteca de destino. "
                    "Elige un origen separado (p. ej. el celular o la tarjeta)."
                )
                return

            kinds = {FileKind.IMAGE}
            if cfg.import_include_videos:
                kinds.add(FileKind.VIDEO)
            opts = ScanOptions(
                follow_symlinks=cfg.follow_symlinks,
                excluded_dir_names=frozenset(cfg.excluded_dir_names),
                include_kinds=frozenset(kinds),
            )

            self.progress.emit("Escaneando el origen…")
            source_files: list = []
            Scanner(self._source, opts, self.controller).scan(
                on_file=source_files.append,
                on_progress=lambda s: self.progress.emit(
                    f"Escaneando el origen… {s.files_found:,} archivos"
                ),
            )
            if self.controller.is_cancelled:
                self.finished.emit([])
                return
            if not source_files:
                self.failed.emit("No se encontraron imágenes ni vídeos en la carpeta de origen.")
                return

            self.progress.emit("Indexando la biblioteca de destino…")
            library_files: list = []
            Scanner(self._library, opts, self.controller).scan(
                on_file=library_files.append,
                on_progress=lambda s: self.progress.emit(
                    f"Indexando la biblioteca… {s.files_found:,} archivos"
                ),
            )
            if self.controller.is_cancelled:
                self.finished.emit([])
                return

            db = None
            if cfg.use_cache:
                try:
                    from app.database.database import FileCacheDB

                    db = FileCacheDB(cfg.effective_db_path())
                    db = db if db.available else None
                except Exception:
                    db = None

            index = LibraryIndex(self._library, db)
            index.build(library_files)
            if cfg.import_match_pixel_identical:
                index.prepare_dims(
                    on_progress=lambda done, total: self.progress.emit(
                        f"Indexando la biblioteca (dimensiones)… {done:,} / {total:,}"
                    ),
                    check=self.controller.checkpoint,
                )
                if self.controller.is_cancelled:
                    self.finished.emit([])
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
                    log.warning("Video probe during import failed: %s", exc)

            self.progress.emit(f"Preparando {len(source_files):,} archivos…")
            resolver = CountryResolver()
            plans = build_import_plan(
                source_files,
                library_root=self._library,
                index=index,
                resolver=resolver,
                config=cfg,
                video_infos=video_infos,
                check=self.controller.checkpoint,
                on_progress=lambda done, total: self.progress.emit(
                    f"Preparando… {done:,} / {total:,}"
                ),
            )
            index.flush()
            if db is not None:
                db.close()
            self.finished.emit(plans)
        except Exception as exc:
            log.exception("Import preview crashed")
            self.failed.emit(str(exc))


class ImportApplyRunner(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)  # ImportReport
    failed = Signal(str)

    def __init__(self, plans, history: OperationHistory, *, undo_of=None) -> None:
        super().__init__(None)
        self._plans = plans
        self._history_path = history.path
        self._undo_of = undo_of
        self.controller = RunController()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="import-apply")
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
                report: ImportReport = undo_import(
                    self._undo_of, history=history, check=self.controller.checkpoint
                )
            else:
                report = apply_import(
                    self._plans,
                    history=history,
                    progress=lambda d, t, n: self.progress.emit(d, t, n),
                    check=self.controller.checkpoint,
                )
            self.finished.emit(report)
        except Exception as exc:
            log.exception("Import apply crashed")
            self.failed.emit(str(exc))
        finally:
            history.close()
