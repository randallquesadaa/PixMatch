"""The "Renombrar por fecha" tab.

Scans a folder (and subfolders), proposes a date-based name for every photo /
video, and renames only what the user confirms. Files stay in their folder -
only the name changes - and every rename is logged and undoable.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.config import AppConfig
from app.core.renamer import (
    PATTERN_PRESETS,
    RenamePlan,
    RenameStatus,
)
from app.database.history import OperationHistory
from app.utils.logging_setup import get_logger
from app.workers.rename_worker import RenameApplyRunner, RenamePreviewRunner

log = get_logger(__name__)


class RenameView(QWidget):
    status_message = Signal(str)

    def __init__(self, config: AppConfig, history: OperationHistory, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.history = history
        self._folder = ""
        self._plans: list[RenamePlan] = []
        self._preview: RenamePreviewRunner | None = None
        self._apply: RenameApplyRunner | None = None
        self._last_report = None

        # -- folder + options row --
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText("Ninguna carpeta seleccionada")
        pick_btn = QPushButton("📁  Seleccionar carpeta")
        pick_btn.clicked.connect(self._choose_folder)

        self.pattern_combo = QComboBox()
        self.pattern_combo.setEditable(True)
        for pat, example in PATTERN_PRESETS:
            self.pattern_combo.addItem(f"{example}    ({pat})", pat)
        idx = self.pattern_combo.findData(config.rename_pattern)
        if idx >= 0:
            self.pattern_combo.setCurrentIndex(idx)
        else:
            self.pattern_combo.setEditText(config.rename_pattern)

        self.prefix_edit = QLineEdit(config.rename_prefix)
        self.prefix_edit.setPlaceholderText("prefijo (opcional), p. ej. IMG_")
        self.prefix_edit.setMaximumWidth(160)

        self.videos_check = QCheckBox("Incluir vídeos")
        self.videos_check.setChecked(config.rename_include_videos)
        self.lower_check = QCheckBox("Extensión en minúsculas")
        self.lower_check.setChecked(config.rename_lowercase_ext)

        self.scan_btn = QPushButton("🔎  Analizar carpeta")
        self.scan_btn.setObjectName("primary")
        self.scan_btn.setEnabled(False)
        self.scan_btn.clicked.connect(self._start_preview)
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)

        row1 = QHBoxLayout()
        row1.addWidget(self.folder_edit, 1)
        row1.addWidget(pick_btn)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Formato:"))
        row2.addWidget(self.pattern_combo, 1)
        row2.addWidget(self.prefix_edit)
        row2.addWidget(self.videos_check)
        row2.addWidget(self.lower_check)
        row3 = QHBoxLayout()
        row3.addWidget(self.scan_btn)
        row3.addWidget(self.cancel_btn)
        row3.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("Hint")

        # -- results table --
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["", "Carpeta", "Nombre actual", "Nombre nuevo", "Fecha", "Origen de la fecha"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)

        self.only_changes = QCheckBox("Mostrar solo los que cambian")
        self.only_changes.setChecked(True)
        self.only_changes.toggled.connect(self._repopulate_table)
        self.select_all_btn = QPushButton("Marcar todos")
        self.select_none_btn = QPushButton("Desmarcar todos")
        self.select_all_btn.clicked.connect(lambda: self._set_all(True))
        self.select_none_btn.clicked.connect(lambda: self._set_all(False))

        table_bar = QHBoxLayout()
        table_bar.addWidget(self.only_changes)
        table_bar.addStretch(1)
        table_bar.addWidget(self.select_all_btn)
        table_bar.addWidget(self.select_none_btn)

        self.summary = QLabel("")
        self.summary.setStyleSheet("font-weight: 600;")
        self.rename_btn = QPushButton("✏️  Renombrar seleccionados…")
        self.rename_btn.setObjectName("primary")
        self.rename_btn.setEnabled(False)
        self.rename_btn.clicked.connect(self._start_apply)
        self.undo_btn = QPushButton("↩  Deshacer último renombrado")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._undo)

        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(self.undo_btn)
        bottom.addWidget(self.rename_btn)

        note = QLabel(
            "Los archivos <b>no se mueven de carpeta</b>: solo cambia el nombre. "
            "Prioridad de fecha: EXIF de captura → EXIF → metadatos de vídeo → "
            "fecha de modificación del archivo. Nunca se sobrescribe un archivo "
            "existente (se añade un sufijo). Todo queda en el historial y se "
            "puede deshacer."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addLayout(row3)
        layout.addWidget(self.progress)
        layout.addWidget(self.progress_label)
        layout.addLayout(table_bar)
        layout.addWidget(self.table, 1)
        layout.addWidget(note)
        layout.addLayout(bottom)

    # ==================================================================
    def set_folder(self, folder: str) -> None:
        self._folder = folder
        self.folder_edit.setText(folder)
        self.scan_btn.setEnabled(bool(folder) and os.path.isdir(folder))

    def _choose_folder(self) -> None:
        start = self._folder or self.config.last_folder or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Carpeta a renombrar", start)
        if folder:
            self.set_folder(folder)

    @property
    def busy(self) -> bool:
        return self._preview is not None or self._apply is not None

    # -- preview --------------------------------------------------
    def _persist_options(self) -> None:
        self.config.rename_pattern = (
            self.pattern_combo.currentData()
            or self.pattern_combo.currentText().strip()
            or "%Y%m%d_%H%M%S"
        )
        self.config.rename_prefix = self.prefix_edit.text().strip()
        self.config.rename_include_videos = self.videos_check.isChecked()
        self.config.rename_lowercase_ext = self.lower_check.isChecked()
        self.config.save()

    def _start_preview(self) -> None:
        if self.busy or not os.path.isdir(self._folder):
            return
        self._persist_options()
        self._plans = []
        self.table.setRowCount(0)
        self.rename_btn.setEnabled(False)
        self.undo_btn.setEnabled(False)
        self._last_report = None
        self._set_running(True)

        runner = RenamePreviewRunner(self._folder, self.config)
        self._preview = runner
        runner.progress.connect(self.progress_label.setText)
        runner.finished.connect(self._on_preview_ready)
        runner.failed.connect(self._on_failed)
        runner.start()

    def _on_preview_ready(self, plans: list) -> None:
        if self._preview is not None:
            self._preview.wait()
            self._preview = None
        self._set_running(False)
        self._plans = plans
        self._repopulate_table()

    def _on_failed(self, msg: str) -> None:
        for r in (self._preview, self._apply):
            if r is not None:
                r.wait()
        self._preview = self._apply = None
        self._set_running(False)
        QMessageBox.critical(self, "Error", msg)

    def _set_running(self, running: bool) -> None:
        self.progress.setVisible(running)
        self.scan_btn.setEnabled(not running and os.path.isdir(self._folder))
        self.cancel_btn.setEnabled(running)
        for w in (self.pattern_combo, self.prefix_edit, self.videos_check, self.lower_check):
            w.setEnabled(not running)
        if not running:
            self.progress_label.setText("")

    def _cancel(self) -> None:
        for r in (self._preview, self._apply):
            if r is not None:
                r.cancel()

    # -- table ----------------------------------------------------
    def _visible_plans(self) -> list[RenamePlan]:
        if self.only_changes.isChecked():
            return [
                p for p in self._plans if p.status in (RenameStatus.RENAME, RenameStatus.SUFFIXED)
            ]
        return list(self._plans)

    def _repopulate_table(self) -> None:
        plans = self._visible_plans()
        self.table.blockSignals(True)
        self.table.setRowCount(len(plans))
        for row, p in enumerate(plans):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            can_change = p.status in (RenameStatus.RENAME, RenameStatus.SUFFIXED)
            chk.setCheckState(Qt.Checked if (p.enabled and can_change) else Qt.Unchecked)
            if not can_change:
                chk.setFlags(Qt.ItemIsEnabled)
            chk.setData(Qt.UserRole, p)
            self.table.setItem(row, 0, chk)

            rel = os.path.relpath(p.directory, self._folder) if self._folder else p.directory
            self.table.setItem(row, 1, QTableWidgetItem("." if rel == "." else rel))
            self.table.setItem(row, 2, QTableWidgetItem(p.old_name))
            new_item = QTableWidgetItem(p.new_name if can_change else "—")
            if p.status is RenameStatus.SUFFIXED:
                new_item.setToolTip("Se añadió un sufijo para no chocar con otro archivo")
            self.table.setItem(row, 3, new_item)
            ts = p.timestamp.strftime("%Y-%m-%d %H:%M:%S") if p.timestamp else "—"
            self.table.setItem(row, 4, QTableWidgetItem(ts))
            src = QTableWidgetItem(
                p.source.label if p.status is not RenameStatus.ERROR else (p.error or "error")
            )
            if not p.source.is_metadata and p.status is not RenameStatus.ERROR:
                src.setToolTip("Sin fecha en los metadatos; se usa la fecha de modificación")
            self.table.setItem(row, 5, src)
        self.table.blockSignals(False)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)
        self.table.resizeColumnToContents(4)
        self._update_summary()

    def _on_item_changed(self, item) -> None:
        if item.column() != 0:
            return
        plan = item.data(Qt.UserRole)
        if isinstance(plan, RenamePlan):
            plan.enabled = item.checkState() == Qt.Checked
            self._update_summary()

    def _set_all(self, value: bool) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(Qt.Checked if value else Qt.Unchecked)
                plan = it.data(Qt.UserRole)
                if isinstance(plan, RenamePlan):
                    plan.enabled = value
        self.table.blockSignals(False)
        self._update_summary()

    def _update_summary(self) -> None:
        will = [p for p in self._plans if p.will_change]
        unchanged = sum(1 for p in self._plans if p.status is RenameStatus.UNCHANGED)
        no_meta = sum(1 for p in will if not p.source.is_metadata)
        errors = sum(1 for p in self._plans if p.status is RenameStatus.ERROR)
        parts = [f"{len(will)} para renombrar"]
        if unchanged:
            parts.append(f"{unchanged} ya con el nombre correcto")
        if no_meta:
            parts.append(f"{no_meta} sin fecha en metadatos (se usa la de modificación)")
        if errors:
            parts.append(f"{errors} con error")
        self.summary.setText(" · ".join(parts) if self._plans else "")
        self.rename_btn.setEnabled(bool(will) and not self.busy)

    # -- apply --------------------------------------------------
    def _start_apply(self) -> None:
        if self.busy:
            return
        planned = [p for p in self._plans if p.will_change]
        if not planned:
            return
        text = (
            f"Vas a renombrar <b>{len(planned)}</b> archivo(s) dentro de:<br>"
            f"<code>{self._folder}</code><br><br>"
            "Los archivos <b>no se mueven de carpeta</b>. La operación queda en el "
            "historial y podrás deshacerla."
        )
        box = QMessageBox(self)
        box.setWindowTitle("Confirmar renombrado")
        box.setIcon(QMessageBox.Question)
        box.setTextFormat(Qt.RichText)
        box.setText(text)
        ok = box.addButton("Renombrar", QMessageBox.AcceptRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not ok:
            return

        self._set_running(True)
        self.progress.setRange(0, len(planned))
        self.rename_btn.setEnabled(False)
        runner = RenameApplyRunner(planned, self.history)
        self._apply = runner
        runner.progress.connect(self._on_apply_progress)
        runner.finished.connect(self._on_apply_done)
        runner.failed.connect(self._on_failed)
        runner.start()

    def _on_apply_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setValue(done)
        self.progress_label.setText(f"Renombrando {done}/{total}…  {name}")

    def _on_apply_done(self, report) -> None:
        if self._apply is not None:
            self._apply.wait()
            self._apply = None
        self._set_running(False)
        self.progress.setRange(0, 0)
        self._last_report = report
        ok = len(report.succeeded)
        failed = report.failed
        self.undo_btn.setEnabled(ok > 0)

        msg = f"Se renombraron {ok} archivo(s)."
        if report.cancelled:
            msg = "Operación cancelada.\n" + msg
        if failed:
            msg += f"\n\n{len(failed)} no se pudieron renombrar:\n"
            msg += "\n".join(f"• {o.error} ({o.old_name})" for o in failed[:8])
            QMessageBox.warning(self, "Renombrado con avisos", msg)
        else:
            QMessageBox.information(self, "Renombrado", msg)
        self.status_message.emit(f"{ok} archivo(s) renombrados.")
        # re-scan so the table reflects the new names
        if ok and os.path.isdir(self._folder):
            self._start_preview()

    def _undo(self) -> None:
        if self.busy or self._last_report is None:
            return
        n = len(self._last_report.succeeded)
        if (
            QMessageBox.question(
                self, "Deshacer", f"¿Devolver {n} archivo(s) a su nombre anterior?"
            )
            != QMessageBox.Yes
        ):
            return
        self._set_running(True)
        self.progress.setRange(0, 0)
        runner = RenameApplyRunner([], self.history, undo_of=self._last_report.succeeded)
        self._apply = runner
        runner.finished.connect(self._on_undo_done)
        runner.failed.connect(self._on_failed)
        runner.start()

    def _on_undo_done(self, report) -> None:
        if self._apply is not None:
            self._apply.wait()
            self._apply = None
        self._set_running(False)
        self.undo_btn.setEnabled(False)
        self._last_report = None
        QMessageBox.information(
            self, "Deshacer", f"Se restauraron {len(report.succeeded)} archivo(s)."
        )
        if os.path.isdir(self._folder):
            self._start_preview()

    def shutdown(self) -> None:
        for r in (self._preview, self._apply):
            if r is not None:
                r.cancel()
                r.wait(2000)
