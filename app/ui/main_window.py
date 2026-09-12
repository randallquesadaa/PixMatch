"""Main application window: folder selection, analysis lifecycle, dashboard
and the duplicate browser."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app import APP_NAME, __version__
from app.config import AppConfig
from app.core.analysis import StepProgress
from app.core.deletion_manager import DeletionMode, build_preview, build_preview_for
from app.core.duplicate_groups import AnalysisResult
from app.core.scanner import ScanStats
from app.database.history import OperationHistory
from app.i18n import tr
from app.ui.deletion_dialog import DeletionConfirmDialog, HistoryDialog
from app.ui.duplicate_view import DuplicateView
from app.ui.import_view import ImportView
from app.ui.organize_view import OrganizeView
from app.ui.rename_view import RenameView
from app.ui.settings_dialog import SettingsDialog
from app.ui.theme import apply_theme
from app.ui.widgets.thumbnail_loader import THUMBNAIL_CACHE
from app.utils.file_utils import human_duration, human_size
from app.utils.logging_setup import LOG_FILE, get_logger
from app.workers.analysis_worker import AnalysisController
from app.workers.deletion_worker import DeletionRunner

log = get_logger(__name__)

_PAGE_WELCOME, _PAGE_PROGRESS, _PAGE_DASHBOARD, _PAGE_DUPLICATES = range(4)


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1280, 860)
        self.setAcceptDrops(True)

        self._folder: str = config.last_folder or ""
        self._result: AnalysisResult | None = None
        self._analysis = AnalysisController(self)
        self._wire_analysis()
        self.history = OperationHistory()
        self._deletion: DeletionRunner | None = None
        self._del_progress = None

        self._build_toolbar()
        self._build_pages()
        self._build_shortcuts()
        self._build_menu()

        self._refresh_toolbar_state()
        self._update_folder_label()

    # ==================================================================
    def _build_toolbar(self) -> None:
        tb = QToolBar("Principal")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.act_pick = QAction(tr("📁  Seleccionar carpeta"), self)
        self.act_analyze = QAction(tr("▶  Analizar"), self)
        self.act_analyze.setToolTip(
            "Análisis incremental: reutiliza la caché para archivos sin cambios."
        )
        self.act_analyze_full = QAction(tr("↻  Análisis completo"), self)
        self.act_analyze_full.setToolTip("Ignora la caché y recalcula todo.")
        self.act_pause = QAction(tr("⏸  Pausar"), self)
        self.act_cancel = QAction(tr("✖  Cancelar"), self)
        self.act_settings = QAction(tr("⚙  Configuración"), self)
        self.act_theme = QAction(tr("🌓  Tema"), self)

        self.act_pick.triggered.connect(self.choose_folder)
        self.act_analyze.triggered.connect(lambda: self.start_analysis(incremental=True))
        self.act_analyze_full.triggered.connect(lambda: self.start_analysis(incremental=False))
        self.act_pause.triggered.connect(self.toggle_pause)
        self.act_cancel.triggered.connect(self.cancel_analysis)
        self.act_settings.triggered.connect(self.open_settings)
        self.act_theme.triggered.connect(self.toggle_theme)

        for a in (
            self.act_pick,
            self.act_analyze,
            self.act_analyze_full,
            self.act_pause,
            self.act_cancel,
        ):
            tb.addAction(a)
        tb.addSeparator()
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        tb.addAction(self.act_theme)
        tb.addAction(self.act_settings)

        self.folder_label = QLabel()
        self.folder_label.setObjectName("Hint")
        self.statusBar().addPermanentWidget(self.folder_label)

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&Archivo")
        m.addAction(self.act_pick)
        m.addAction(self.act_analyze)
        m.addAction(self.act_analyze_full)
        m.addSeparator()
        export_menu = m.addMenu("Exportar resultados")
        self._act_export_csv = QAction("CSV…", self)
        self._act_export_json = QAction("JSON…", self)
        self._act_export_html = QAction("Informe HTML…", self)
        self._act_export_csv.triggered.connect(lambda: self._export("csv"))
        self._act_export_json.triggered.connect(lambda: self._export("json"))
        self._act_export_html.triggered.connect(lambda: self._export("html"))
        for a in (self._act_export_csv, self._act_export_json, self._act_export_html):
            a.setEnabled(False)
            export_menu.addAction(a)
        m.addSeparator()
        exit_act = QAction("Salir", self)
        exit_act.triggered.connect(self.close)
        m.addAction(exit_act)

        h = self.menuBar().addMenu("A&yuda")
        history_act = QAction("Historial de operaciones…", self)
        history_act.triggered.connect(self._show_history)
        h.addAction(history_act)
        logs_act = QAction("Abrir carpeta de logs", self)
        logs_act.triggered.connect(lambda: self._open_path(str(LOG_FILE.parent)))
        h.addAction(logs_act)
        about_act = QAction("Acerca de", self)
        about_act.triggered.connect(self._about)
        h.addAction(about_act)

    def _build_pages(self) -> None:
        self.stack = QStackedWidget()
        self.stack.addWidget(self._welcome_page())  # 0
        self.stack.addWidget(self._progress_page())  # 1
        self.stack.addWidget(self._dashboard_page())  # 2
        self.duplicate_view = DuplicateView()
        self.duplicate_view.decisions_changed.connect(self._on_decisions_changed)
        self.duplicate_view.deletion_requested.connect(self._perform_deletion)
        self.duplicate_view.single_deletion_requested.connect(self._perform_single_deletion)
        self.stack.addWidget(self.duplicate_view)  # 3
        self.stack.setCurrentIndex(_PAGE_WELCOME)

        self.rename_view = RenameView(self.config, self.history)
        self.rename_view.status_message.connect(lambda m: self.statusBar().showMessage(m, 6000))

        self.import_view = ImportView(self.config, self.history)
        self.import_view.status_message.connect(lambda m: self.statusBar().showMessage(m, 6000))

        self.organize_view = OrganizeView(self.config, self.history)
        self.organize_view.status_message.connect(lambda m: self.statusBar().showMessage(m, 6000))

        self.tabs = QTabWidget()
        self.tabs.addTab(self.stack, "🔍  Duplicados")
        self.tabs.addTab(self.rename_view, "✏️  Renombrar por fecha")
        self.tabs.addTab(self.import_view, "📥  Importar y organizar")
        self.tabs.addTab(self.organize_view, "🗂️  Organizar biblioteca")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self.tabs)

    def _build_shortcuts(self) -> None:
        def sc(seq: str, fn) -> None:
            QShortcut(QKeySequence(seq), self, activated=fn)

        sc("Right", lambda: self._on_dupe_page() and self.duplicate_view.next_group())
        sc("Left", lambda: self._on_dupe_page() and self.duplicate_view.previous_group())
        sc("M", lambda: self._on_dupe_page() and self.duplicate_view.act_keep())
        sc("D", lambda: self._on_dupe_page() and self.duplicate_view.act_delete())
        sc("A", lambda: self._on_dupe_page() and self.duplicate_view.keep_all())
        sc("I", lambda: self._on_dupe_page() and self.duplicate_view.ignore_group())
        sc("Escape", self._on_escape)

    # ------------------------------------------------------------------
    def _welcome_page(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setAlignment(Qt.AlignCenter)

        title = QLabel("PixMatch")
        title.setStyleSheet("font-size: 28px; font-weight: 800;")
        title.setAlignment(Qt.AlignCenter)

        sub = QLabel(
            "Herramienta de solo lectura para encontrar imágenes y videos duplicados.\n"
            "Nunca se borra ni se modifica nada sin tu confirmación explícita."
        )
        sub.setObjectName("Hint")
        sub.setAlignment(Qt.AlignCenter)

        drop = QLabel("⬇  Arrastra una carpeta aquí para comenzar")
        drop.setObjectName("DropZone")
        drop.setAlignment(Qt.AlignCenter)
        drop.setMinimumSize(560, 220)

        pick = QPushButton("Seleccionar carpeta")
        pick.setObjectName("primary")
        pick.clicked.connect(self.choose_folder)

        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addSpacing(20)
        layout.addWidget(drop, 0, Qt.AlignCenter)
        layout.addSpacing(16)
        layout.addWidget(pick, 0, Qt.AlignCenter)
        return w

    def _progress_page(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setAlignment(Qt.AlignCenter)

        self.progress_title = QLabel("Preparando…")
        self.progress_title.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.progress_title.setAlignment(Qt.AlignCenter)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumWidth(560)
        self.progress_bar.setRange(0, 0)  # indeterminate during scan

        self.progress_detail = QLabel("")
        self.progress_detail.setObjectName("Hint")
        self.progress_detail.setAlignment(Qt.AlignCenter)

        self.progress_message = QLabel("")
        self.progress_message.setObjectName("Hint")
        self.progress_message.setAlignment(Qt.AlignCenter)

        btns = QHBoxLayout()
        self.pause_btn = QPushButton("Pausar")
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setObjectName("danger")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.cancel_btn.clicked.connect(self.cancel_analysis)
        btns.addStretch(1)
        btns.addWidget(self.pause_btn)
        btns.addWidget(self.cancel_btn)
        btns.addStretch(1)

        layout.addWidget(self.progress_title)
        layout.addSpacing(10)
        layout.addWidget(self.progress_bar, 0, Qt.AlignCenter)
        layout.addWidget(self.progress_detail)
        layout.addWidget(self.progress_message)
        layout.addSpacing(16)
        layout.addLayout(btns)
        return w

    def _dashboard_page(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setAlignment(Qt.AlignCenter)

        self.dash_title = QLabel("Resultados")
        self.dash_title.setStyleSheet("font-size: 24px; font-weight: 800;")
        self.dash_title.setAlignment(Qt.AlignCenter)

        self.dash_grid = QGridLayout()
        self.dash_grid.setHorizontalSpacing(28)
        self.dash_grid.setVerticalSpacing(8)
        grid_holder = QWidget()
        grid_holder.setLayout(self.dash_grid)

        self.dash_reclaim = QLabel("")
        self.dash_reclaim.setStyleSheet("font-size: 18px; font-weight: 700;")
        self.dash_reclaim.setAlignment(Qt.AlignCenter)

        self.dash_issues = QLabel("")
        self.dash_issues.setObjectName("Hint")
        self.dash_issues.setAlignment(Qt.AlignCenter)

        btns = QHBoxLayout()
        self.btn_view_dupes = QPushButton("Ver duplicados")
        self.btn_view_dupes.setObjectName("primary")
        self.btn_view_identical = QPushButton("Ver idénticos")
        self.btn_view_similar = QPushButton("Ver similares")
        self.btn_view_videos = QPushButton("Ver videos")
        self.btn_rescan = QPushButton("Analizar de nuevo")
        self.btn_view_dupes.clicked.connect(lambda: self._open_duplicates("Todas"))
        self.btn_view_identical.clicked.connect(lambda: self._open_duplicates("Archivo idéntico"))
        self.btn_view_similar.clicked.connect(lambda: self._open_duplicates("Similares"))
        self.btn_view_videos.clicked.connect(lambda: self._open_duplicates("Videos"))
        self.btn_rescan.clicked.connect(lambda: self.start_analysis(incremental=True))
        for b in (
            self.btn_view_dupes,
            self.btn_view_identical,
            self.btn_view_similar,
            self.btn_view_videos,
            self.btn_rescan,
        ):
            btns.addWidget(b)

        outer.addWidget(self.dash_title)
        outer.addSpacing(16)
        outer.addWidget(grid_holder, 0, Qt.AlignCenter)
        outer.addSpacing(16)
        outer.addWidget(self.dash_reclaim)
        outer.addWidget(self.dash_issues)
        outer.addSpacing(20)
        outer.addLayout(btns)
        return w

    # ==================================================================
    # folder selection
    def choose_folder(self) -> None:
        start = self._folder or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Seleccionar carpeta", start)
        if folder:
            self._set_folder(folder)

    def _set_folder(self, folder: str) -> None:
        self._folder = folder
        self.config.last_folder = folder
        self.config.save()
        self._update_folder_label()
        self._refresh_toolbar_state()
        if hasattr(self, "rename_view"):
            self.rename_view.set_folder(folder)
        if hasattr(self, "import_view"):
            self.import_view.set_source_folder(folder)
        self.statusBar().showMessage(f"Carpeta lista: {folder}. Pulsa «Analizar».", 6000)

    def _on_tab_changed(self, index: int) -> None:
        on_dupes = index == 0
        for a in (self.act_analyze, self.act_analyze_full, self.act_pause, self.act_cancel):
            a.setVisible(on_dupes)
        if index == 1 and self._folder and not self.rename_view._folder:
            self.rename_view.set_folder(self._folder)
        if index == 2 and self._folder:
            self.import_view.set_source_folder(self._folder)

    def _update_folder_label(self) -> None:
        self.folder_label.setText(
            f"📁 {self._folder}" if self._folder else "Ninguna carpeta seleccionada"
        )

    # drag & drop
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and os.path.isdir(url.toLocalFile()):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                self._set_folder(path)
                if not self._analysis.running:
                    self.start_analysis(incremental=False)
                return

    # ==================================================================
    # analysis lifecycle
    def _wire_analysis(self) -> None:
        self._analysis.phase_changed.connect(self._on_phase)
        self._analysis.scan_progress.connect(self._on_scan_progress)
        self._analysis.step_progress.connect(self._on_step_progress)
        self._analysis.message.connect(self._on_worker_message)
        self._analysis.finished.connect(self._on_finished)
        self._analysis.failed.connect(self._on_failed)

    def start_analysis(self, incremental: bool = False) -> None:
        if self._analysis.running:
            return
        if not self._folder or not os.path.isdir(self._folder):
            QMessageBox.warning(self, "Sin carpeta", "Primero selecciona una carpeta válida.")
            return

        self.progress_title.setText("Analizando…")
        self.progress_bar.setRange(0, 0)
        self.progress_detail.setText("")
        self.progress_message.setText("")
        self.pause_btn.setText("Pausar")
        self.stack.setCurrentIndex(_PAGE_PROGRESS)
        self._refresh_toolbar_state(running=True)
        log.info("Starting analysis: %s (incremental=%s)", self._folder, incremental)
        self._analysis.start(self._folder, self.config, incremental)

    def toggle_pause(self) -> None:
        if not self._analysis.running:
            return
        if self._analysis.paused:
            self._analysis.resume()
            self.pause_btn.setText("Pausar")
            self.act_pause.setText("⏸  Pausar")
            self.progress_title.setText("Analizando…")
        else:
            self._analysis.pause()
            self.pause_btn.setText("Reanudar")
            self.act_pause.setText("▶  Reanudar")
            self.progress_title.setText("En pausa")

    def cancel_analysis(self) -> None:
        if not self._analysis.running:
            return
        if (
            QMessageBox.question(
                self, "Cancelar análisis", "¿Seguro que quieres cancelar el análisis en curso?"
            )
            == QMessageBox.Yes
        ):
            self.progress_title.setText("Cancelando…")
            self._analysis.cancel()

    def _on_phase(self, phase: str) -> None:
        labels = {
            "scanning": "Escaneando carpetas…",
            "hashing": "Calculando SHA-256…",
            "pixels": "Comparando píxeles…",
            "perceptual": "Calculando firmas de imagen…",
            "embeddings": "Calculando embeddings visuales (IA)…",
            "crops": "Buscando recortes…",
            "video_metadata": "Leyendo metadatos de vídeo…",
            "video_frames": "Comparando fotogramas de vídeo…",
            "grouping": "Agrupando duplicados…",
            "done": "Finalizando…",
        }
        self.progress_title.setText(labels.get(phase, phase))
        if phase == "scanning":
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)

    def _on_scan_progress(self, stats: ScanStats) -> None:
        self.progress_detail.setText(
            f"Carpetas: {stats.folders_scanned:,}   ·   "
            f"Archivos: {stats.files_found:,}   ·   "
            f"Imágenes: {stats.images:,}   ·   Videos: {stats.videos:,}   ·   "
            f"Tamaño: {human_size(stats.total_size)}"
            + (f"   ·   {len(stats.issues)} avisos" if stats.issues else "")
        )

    def _on_step_progress(self, p: StepProgress) -> None:
        pct = int(p.done * 100 / p.total) if p.total else 0
        self.progress_bar.setValue(pct)
        groups = f"   ·   Grupos: {p.groups_so_far:,}" if p.groups_so_far else ""
        self.progress_detail.setText(
            f"{p.label}: {p.done:,} / {p.total:,}{groups}   ·   "
            f"Transcurrido: {human_duration(p.elapsed)}   ·   "
            f"Restante aprox.: {human_duration(p.eta_seconds)}"
        )

    def _on_worker_message(self, text: str) -> None:
        self.progress_message.setText(text)

    def _on_finished(self, result: AnalysisResult) -> None:
        self._result = result
        self._refresh_toolbar_state(running=False)
        THUMBNAIL_CACHE.clear_memory()
        if result.cancelled:
            self.statusBar().showMessage(
                "Análisis cancelado. Se muestran resultados parciales.", 8000
            )
        self._populate_dashboard(result)
        self.duplicate_view.load(result)
        self.stack.setCurrentIndex(_PAGE_DASHBOARD)
        has_groups = bool(result.groups)
        for a in (self._act_export_csv, self._act_export_json, self._act_export_html):
            a.setEnabled(has_groups)
        log.info(
            "Analysis done: %d groups, %d redundant copies, %s reclaimable",
            len(result.groups),
            result.redundant_copies,
            human_size(result.reclaimable_bytes),
        )

    def _on_failed(self, msg: str) -> None:
        self._refresh_toolbar_state(running=False)
        self.stack.setCurrentIndex(_PAGE_WELCOME if self._result is None else _PAGE_DASHBOARD)
        QMessageBox.critical(self, "Error en el análisis", msg)

    # ==================================================================
    def _populate_dashboard(self, r: AnalysisResult) -> None:
        s: ScanStats = r.scan_stats
        rows = [
            ("Archivos analizados", f"{s.files_found:,}"),
            ("Imágenes", f"{s.images:,}"),
            ("Videos", f"{s.videos:,}"),
            ("Otros archivos", f"{s.others:,}"),
            ("Carpetas analizadas", f"{s.folders_scanned:,}"),
            ("Tamaño total", human_size(s.total_size)),
            ("—", "—"),
            ("Archivos hasheados (SHA-256)", f"{r.hashed_files:,}"),
            ("Imágenes decodificadas (pixel)", f"{r.decoded_files:,}"),
            ("Firmas de imagen calculadas", f"{r.perceptual_files:,}"),
            ("Recortes detectados (pares)", f"{r.crop_pairs_found:,}"),
            ("Vídeos analizados (metadatos)", f"{r.videos_probed:,}"),
            ("Vídeos con fotogramas muestreados", f"{r.video_frames_sampled:,}"),
            ("Reutilizados de la caché", f"{r.cache_hits:,}"),
            ("Sensibilidad de detección", str(r.sensitivity).capitalize()),
            ("—", "—"),
            (
                "Grupos de duplicados",
                f"{len(r.unresolved_groups):,} sin resolver / {len(r.groups):,} total",
            ),
            (
                "  🟢 Archivo idéntico",
                f"{len(r.file_identical_groups):,} grupos · {r.identical_file_count:,} archivos",
            ),
            (
                "  🔵 Pixel por pixel idéntico",
                f"{len(r.pixel_identical_groups):,} grupos · {r.pixel_identical_file_count:,} archivos",
            ),
            ("  🟦 Redimensionados", f"{len(r.resized_groups):,} grupos"),
            ("  🟧 Recortes", f"{len(r.cropped_groups):,} grupos"),
            (
                "  🟡 Similares (todas las categorías)",
                f"{len(r.similar_groups):,} grupos · {r.similar_file_count:,} archivos",
            ),
            ("  · grupos de videos", f"{len(r.video_groups):,}"),
            ("Copias redundantes (dejando 1 por grupo)", f"{r.redundant_copies:,}"),
        ]
        if r.deleted_file_count:
            rows.append(("Eliminados en esta sesión", f"{r.deleted_file_count:,}"))
        rows.append(("Tiempo de análisis", human_duration(r.elapsed_seconds)))
        # clear grid
        while self.dash_grid.count():
            item = self.dash_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for i, (k, v) in enumerate(rows):
            lk = QLabel(k)
            lk.setObjectName("Hint")
            lv = QLabel(v)
            lv.setStyleSheet("font-weight: 700;")
            lv.setAlignment(Qt.AlignRight)
            self.dash_grid.addWidget(lk, i, 0)
            self.dash_grid.addWidget(lv, i, 1)

        self.dash_reclaim.setText(
            f"Espacio potencial recuperable: {human_size(r.reclaimable_bytes)}"
        )
        notes = []
        if s.issues:
            notes.append(
                f"⚠️ {len(s.issues)} avisos durante el escaneo "
                f"(permisos, archivos ilegibles…). Consulta Configuración → Logs."
            )
        if self.config.analyze_videos and not r.ffmpeg_available:
            from app.core.ffmpeg import install_hint

            notes.append("⚠️ FFmpeg no disponible: no se analizaron los vídeos. " + install_hint())
        self.dash_issues.setText("\n".join(notes))

        self.btn_view_dupes.setEnabled(len(r.groups) > 0)
        self.btn_view_videos.setEnabled(len(r.video_groups_all) > 0)
        self.btn_view_similar.setEnabled(len(r.similar_groups) > 0)
        self.btn_view_identical.setEnabled(
            len(r.file_identical_groups) + len(r.pixel_identical_groups) > 0
        )
        self.dash_reclaim.setToolTip(
            "Estimación máxima: conservar el archivo más grande de cada grupo."
        )

    def _open_duplicates(self, filter_name: str) -> None:
        if not self._result or not self._result.groups:
            QMessageBox.information(
                self, "Sin duplicados", "No se encontraron grupos de duplicados exactos."
            )
            return
        idx = self.duplicate_view.filter_combo.findData(filter_name)
        if idx < 0:
            idx = self.duplicate_view.filter_combo.findText(filter_name)
        if idx >= 0:
            self.duplicate_view.filter_combo.setCurrentIndex(idx)
        self.stack.setCurrentIndex(_PAGE_DUPLICATES)

    def _on_decisions_changed(self) -> None:
        if not self._result:
            return
        marked = [
            r
            for g in self._result.groups
            if not g.ignored
            for r in g.marked_for_deletion
            if not r.deleted
        ]
        freed = sum(r.size for r in marked)
        if marked:
            self.statusBar().showMessage(
                f"Marcados para eliminar: {len(marked)} archivo(s) · liberaría {human_size(freed)}."
            )
        else:
            self.statusBar().clearMessage()

    # ==================================================================
    # deletion
    def _perform_deletion(self) -> None:
        if not self._result:
            return
        preview = build_preview(self._result.groups)
        if preview.count == 0:
            QMessageBox.information(
                self,
                "Nada que eliminar",
                "No hay archivos marcados para eliminar. Marca archivos con "
                "«Marcar para eliminar» o «Eliminar no seleccionados».",
            )
            return
        self._confirm_and_run(preview)

    def _perform_single_deletion(self, record) -> None:
        if not self._result:
            return
        group = next(
            (g for g in self._result.groups if any(r is record for r in g.records)),
            None,
        )
        preview = build_preview_for([(record, group)] if group else [], record)
        if preview.count == 0:
            return
        self._confirm_and_run(preview)

    def _confirm_and_run(self, preview) -> None:
        if self._deletion is not None:
            return
        default_mode = (
            DeletionMode.PERMANENT
            if self.config.deletion_mode == "permanent"
            else DeletionMode.TRASH
        )
        dlg = DeletionConfirmDialog(preview, default_mode, self)
        if not dlg.exec() or dlg.chosen_mode is None:
            return
        mode = dlg.chosen_mode

        planned = preview.items
        self._del_progress = QProgressDialog(
            "Eliminando archivos…", "Cancelar", 0, len(planned), self
        )
        self._del_progress.setWindowTitle("Eliminando")
        self._del_progress.setWindowModality(Qt.WindowModal)
        self._del_progress.setMinimumDuration(0 if len(planned) > 3 else 400)

        runner = DeletionRunner(planned, mode, self.history)
        self._deletion = runner
        runner.progress.connect(self._on_deletion_progress)
        runner.finished.connect(self._on_deletion_finished)
        runner.failed.connect(self._on_deletion_failed)
        self._del_progress.canceled.connect(runner.cancel)
        runner.start()

    def _on_deletion_progress(self, done: int, total: int, path: str) -> None:
        if self._del_progress is not None:
            self._del_progress.setValue(done)
            self._del_progress.setLabelText(
                f"Eliminando ({done}/{total})…\n{os.path.basename(path)}"
            )

    def _on_deletion_finished(self, report) -> None:
        if self._del_progress is not None:
            self._del_progress.close()
            self._del_progress = None
        if self._deletion is not None:
            self._deletion.wait()
            self._deletion = None

        self.duplicate_view.after_deletion()
        if self._result is not None:
            self._populate_dashboard(self._result)

        freed = human_size(report.freed_bytes)
        ok = len(report.succeeded)
        failed = report.failed
        verb = "movieron a la papelera" if report.mode is DeletionMode.TRASH else "eliminaron"
        msg = f"Se {verb} {ok} archivo(s).\nEspacio recuperado: {freed}."
        if report.cancelled:
            msg = "Operación cancelada.\n" + msg
        if failed:
            msg += f"\n\n{len(failed)} no se pudieron eliminar:\n"
            msg += "\n".join(f"• {o.error} ({os.path.basename(o.path)})" for o in failed[:8])
            if len(failed) > 8:
                msg += f"\n… y {len(failed) - 8} más. Consulta el historial."
            QMessageBox.warning(self, "Eliminación finalizada con avisos", msg)
        else:
            QMessageBox.information(self, "Eliminación finalizada", msg)
        self.statusBar().showMessage(f"{ok} archivo(s) eliminados · {freed} recuperados.", 8000)

    def _on_deletion_failed(self, msg: str) -> None:
        if self._del_progress is not None:
            self._del_progress.close()
            self._del_progress = None
        if self._deletion is not None:
            self._deletion.wait()
            self._deletion = None
        QMessageBox.critical(self, "Error al eliminar", msg)

    def _show_history(self) -> None:
        HistoryDialog(self.history, self).exec()

    def _export(self, fmt: str) -> None:
        if not self._result or not self._result.groups:
            return
        from app.core import export as export_mod

        filters = {
            "csv": ("CSV (*.csv)", ".csv"),
            "json": ("JSON (*.json)", ".json"),
            "html": ("Informe HTML (*.html)", ".html"),
        }
        flt, ext = filters[fmt]
        default = os.path.join(os.path.expanduser("~"), f"duplicados{ext}")
        path, _ = QFileDialog.getSaveFileName(self, "Exportar resultados", default, flt)
        if not path:
            return
        if not path.lower().endswith(ext):
            path += ext
        try:
            fn = {
                "csv": export_mod.export_csv,
                "json": export_mod.export_json,
                "html": export_mod.export_html,
            }[fmt]
            fn(self._result, path)
        except Exception as exc:
            log.exception("Export failed")
            QMessageBox.critical(self, "Error al exportar", str(exc))
            return
        self.statusBar().showMessage(f"Exportado a {path}", 6000)
        if (
            QMessageBox.question(self, "Exportado", f"Guardado en:\n{path}\n\n¿Abrir la carpeta?")
            == QMessageBox.Yes
        ):
            self._open_path(path)

    # ==================================================================
    def _on_dupe_page(self) -> bool:
        return self.tabs.currentIndex() == 0 and self.stack.currentIndex() == _PAGE_DUPLICATES

    def _on_escape(self) -> None:
        if self._on_dupe_page():
            self.stack.setCurrentIndex(_PAGE_DASHBOARD)

    def open_settings(self) -> None:
        from app.i18n import set_language

        before_lang = self.config.language
        dlg = SettingsDialog(self.config, self)
        if dlg.exec():
            apply_theme(self._app(), self.config.theme)
            set_language(self.config.language)
            self.statusBar().showMessage("Configuración guardada.", 4000)
            if self.config.language != before_lang:
                QMessageBox.information(
                    self,
                    "Idioma",
                    "El idioma cambiará por completo al reiniciar la aplicación.",
                )

    def toggle_theme(self) -> None:
        self.config.theme = "light" if self.config.theme == "dark" else "dark"
        self.config.save()
        apply_theme(self._app(), self.config.theme)

    def _about(self) -> None:
        QMessageBox.information(
            self,
            "Acerca de",
            f"{APP_NAME} {__version__}\n\n"
            "Fases 1-3:\n"
            "• Escaneo recursivo + SHA-256 (archivo idéntico)\n"
            "• Comparación pixel-por-pixel y hash perceptual (similares)\n"
            "• Caché SQLite con análisis incremental\n"
            "• Eliminación segura a la papelera del sistema, con confirmación,\n"
            "  historial de operaciones y exportación CSV / JSON / HTML\n\n"
            "La aplicación solo lee tus archivos. Nada se elimina sin una acción\n"
            "explícita y confirmada; la opción por defecto es mover a la papelera.",
        )

    # ------------------------------------------------------------------
    def _app(self):
        from PySide6.QtWidgets import QApplication

        return QApplication.instance()

    def _open_path(self, path: str) -> None:
        from app.utils.file_utils import open_in_file_manager

        open_in_file_manager(path)

    def _refresh_toolbar_state(self, running: bool | None = None) -> None:
        if running is None:
            running = self._analysis.running
        has_folder = bool(self._folder) and os.path.isdir(self._folder)
        self.act_pick.setEnabled(not running)
        self.act_analyze.setEnabled(has_folder and not running)
        self.act_analyze_full.setEnabled(has_folder and not running)
        self.act_pause.setEnabled(running)
        self.act_cancel.setEnabled(running)

    def closeEvent(self, event) -> None:
        self._analysis.shutdown()
        if self._deletion is not None:
            self._deletion.cancel()
            self._deletion.wait()
        if hasattr(self, "rename_view"):
            self.rename_view.shutdown()
        if hasattr(self, "import_view"):
            self.import_view.shutdown()
        if hasattr(self, "organize_view"):
            self.organize_view.shutdown()
        self.history.close()
        self.config.save()
        super().closeEvent(event)
