"""The "Importar y organizar" tab.

Moves photos / videos from a source folder (a phone, an SD card) into an
ordered library: ``<country>/<YYYY>/<YYYY-MM>/<date-name>``. The country comes
from each file's own GPS metadata (offline). Files already in the library are
detected and skipped. Every move is verified, logged and undoable.
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
from app.core.importer import ImportPlan, ImportStatus, LocationSource
from app.core.renamer import PATTERN_PRESETS
from app.database.history import OperationHistory
from app.utils.file_utils import human_size
from app.utils.logging_setup import get_logger
from app.workers.import_worker import ImportApplyRunner, ImportPreviewRunner

log = get_logger(__name__)

_STATUS_COLORS = {
    ImportStatus.NEW: "#2e7d32",
    ImportStatus.NEW_SUFFIXED: "#b26a00",
    ImportStatus.DUPLICATE_EXACT: "#607d8b",
    ImportStatus.DUPLICATE_PIXEL: "#1565c0",
    ImportStatus.DUPLICATE_IN_BATCH: "#607d8b",
    ImportStatus.ERROR: "#c62828",
}


class ImportView(QWidget):
    status_message = Signal(str)

    def __init__(self, config: AppConfig, history: OperationHistory, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.history = history
        self._source = config.import_source_folder or ""
        self._library = config.import_library_folder or ""
        self._plans: list[ImportPlan] = []
        self._preview: ImportPreviewRunner | None = None
        self._apply: ImportApplyRunner | None = None
        self._last_report = None

        # -- source + library rows --
        self.source_edit = QLineEdit(self._source)
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText("Carpeta de origen (el celular, la tarjeta…)")
        src_btn = QPushButton("📁  Origen")
        src_btn.clicked.connect(self._choose_source)

        self.library_edit = QLineEdit(self._library)
        self.library_edit.setReadOnly(True)
        self.library_edit.setPlaceholderText("Carpeta raíz de la biblioteca de fotos")
        lib_btn = QPushButton("📁  Biblioteca")
        lib_btn.clicked.connect(self._choose_library)

        self.pattern_combo = QComboBox()
        self.pattern_combo.setEditable(True)
        for pat, example in PATTERN_PRESETS:
            self.pattern_combo.addItem(f"{example}    ({pat})", pat)
        idx = self.pattern_combo.findData(config.import_pattern)
        if idx >= 0:
            self.pattern_combo.setCurrentIndex(idx)
        else:
            self.pattern_combo.setEditText(config.import_pattern)

        self.videos_check = QCheckBox("Incluir vídeos")
        self.videos_check.setChecked(config.import_include_videos)
        self.no_loc_edit = QLineEdit(config.import_no_location_label)
        self.no_loc_edit.setMaximumWidth(180)

        self.scan_btn = QPushButton("🔎  Analizar")
        self.scan_btn.setObjectName("primary")
        self.scan_btn.clicked.connect(self._start_preview)
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Origen:"))
        row1.addWidget(self.source_edit, 1)
        row1.addWidget(src_btn)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Biblioteca:"))
        row2.addWidget(self.library_edit, 1)
        row2.addWidget(lib_btn)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Formato:"))
        row3.addWidget(self.pattern_combo, 1)
        row3.addWidget(self.videos_check)
        row3.addWidget(QLabel("Sin ubicación:"))
        row3.addWidget(self.no_loc_edit)
        row4 = QHBoxLayout()
        row4.addWidget(self.scan_btn)
        row4.addWidget(self.cancel_btn)
        row4.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("Hint")

        # -- results table --
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["", "Origen", "Fecha", "País", "Carpeta destino", "Nombre destino", "Estado"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)

        self.only_new = QCheckBox("Mostrar solo los nuevos")
        self.only_new.setChecked(False)
        self.only_new.toggled.connect(self._repopulate_table)
        self.select_all_btn = QPushButton("Marcar todos")
        self.select_none_btn = QPushButton("Desmarcar todos")
        self.select_all_btn.clicked.connect(lambda: self._set_all(True))
        self.select_none_btn.clicked.connect(lambda: self._set_all(False))

        table_bar = QHBoxLayout()
        table_bar.addWidget(self.only_new)
        table_bar.addStretch(1)
        table_bar.addWidget(self.select_all_btn)
        table_bar.addWidget(self.select_none_btn)

        self.summary = QLabel("")
        self.summary.setStyleSheet("font-weight: 600;")
        self.move_btn = QPushButton("📥  Mover a la biblioteca…")
        self.move_btn.setObjectName("primary")
        self.move_btn.setEnabled(False)
        self.move_btn.clicked.connect(self._start_apply)
        self.undo_btn = QPushButton("↩  Deshacer última importación")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._undo)

        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(self.undo_btn)
        bottom.addWidget(self.move_btn)

        note = QLabel(
            "El país sale del <b>GPS del propio archivo</b> (sin conexión); si no hay GPS "
            "va a «Sin ubicación». Estructura: <code>&lt;País&gt;/&lt;Año&gt;/&lt;Año-Mes&gt;/</code>. "
            "Mover = copiar → verificar → borrar el origen; si algo falla, el original "
            "no se toca. Los archivos que <b>ya están en la biblioteca</b> no se mueven "
            "(marca la fila para borrarlos del origen). Todo queda en el historial y se "
            "puede deshacer."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(row1)
        layout.addLayout(row2)
        layout.addLayout(row3)
        layout.addLayout(row4)
        layout.addWidget(self.progress)
        layout.addWidget(self.progress_label)
        layout.addLayout(table_bar)
        layout.addWidget(self.table, 1)
        layout.addWidget(note)
        layout.addLayout(bottom)

        self._refresh_scan_enabled()

    # ==================================================================
    def set_source_folder(self, folder: str) -> None:
        if folder and not self._source:
            self._source = folder
            self.source_edit.setText(folder)
            self._refresh_scan_enabled()

    def _choose_source(self) -> None:
        start = self._source or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Carpeta de origen", start)
        if folder:
            self._source = folder
            self.source_edit.setText(folder)
            self.config.import_source_folder = folder
            self.config.save()
            self._refresh_scan_enabled()

    def _choose_library(self) -> None:
        start = self._library or self._source or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Carpeta de la biblioteca", start)
        if folder:
            self._library = folder
            self.library_edit.setText(folder)
            self.config.import_library_folder = folder
            self.config.save()
            self._refresh_scan_enabled()

    def _refresh_scan_enabled(self) -> None:
        ok = (
            bool(self._source)
            and os.path.isdir(self._source)
            and bool(self._library)
            and os.path.isdir(self._library)
        )
        self.scan_btn.setEnabled(ok and not self.busy)

    @property
    def busy(self) -> bool:
        return self._preview is not None or self._apply is not None

    # -- preview -------------------------------------------------------
    def _persist_options(self) -> None:
        self.config.import_pattern = (
            self.pattern_combo.currentData()
            or self.pattern_combo.currentText().strip()
            or "%Y%m%d_%H%M%S"
        )
        self.config.import_include_videos = self.videos_check.isChecked()
        self.config.import_no_location_label = self.no_loc_edit.text().strip() or "Sin ubicación"
        self.config.save()

    def _start_preview(self) -> None:
        if self.busy or not os.path.isdir(self._source) or not os.path.isdir(self._library):
            return
        self._persist_options()
        self._plans = []
        self.table.setRowCount(0)
        self.move_btn.setEnabled(False)
        self._set_running(True)

        runner = ImportPreviewRunner(self._source, self._library, self.config)
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
        self.cancel_btn.setEnabled(running)
        for w in (self.pattern_combo, self.videos_check, self.no_loc_edit):
            w.setEnabled(not running)
        self.scan_btn.setEnabled(not running and self._can_scan())
        if not running:
            self.progress_label.setText("")

    def _can_scan(self) -> bool:
        return (
            bool(self._source)
            and os.path.isdir(self._source)
            and bool(self._library)
            and os.path.isdir(self._library)
        )

    def _cancel(self) -> None:
        for r in (self._preview, self._apply):
            if r is not None:
                r.cancel()

    # -- table --------------------------------------------------------
    def _visible_plans(self) -> list[ImportPlan]:
        if self.only_new.isChecked():
            return [p for p in self._plans if p.status.is_new]
        return list(self._plans)

    def _repopulate_table(self) -> None:
        plans = self._visible_plans()
        self.table.blockSignals(True)
        self.table.setRowCount(len(plans))
        for row, p in enumerate(plans):
            chk = QTableWidgetItem()
            actionable = p.status.is_new or p.status.is_duplicate
            if actionable:
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                default_on = p.status.is_new
                chk.setCheckState(Qt.Checked if (p.enabled and default_on) else Qt.Unchecked)
                p.enabled = chk.checkState() == Qt.Checked
                p.delete_source_if_dup = p.status.is_duplicate and p.enabled
                chk.setToolTip(
                    "Mover a la biblioteca"
                    if p.status.is_new
                    else "Ya está en la biblioteca — marcar = borrar del origen"
                )
            else:
                chk.setFlags(Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Unchecked)
            chk.setData(Qt.UserRole, p)
            self.table.setItem(row, 0, chk)

            self.table.setItem(row, 1, QTableWidgetItem(os.path.basename(p.source_path)))
            ts = p.timestamp.strftime("%Y-%m-%d %H:%M:%S") if p.timestamp else "—"
            self.table.setItem(row, 2, QTableWidgetItem(ts))

            marker = "📍 " if p.location_source is not LocationSource.NONE else ""
            country_item = QTableWidgetItem(f"{marker}{p.country}")
            country_item.setToolTip(p.location_source.label)
            self.table.setItem(row, 3, country_item)

            rel = os.path.relpath(p.dest_dir, self._library) if p.dest_dir else "—"
            self.table.setItem(row, 4, QTableWidgetItem(rel))
            self.table.setItem(row, 5, QTableWidgetItem(p.dest_name if p.status.is_new else "—"))

            status_text = p.status.label
            if p.status is ImportStatus.ERROR and p.error:
                status_text = p.error
            elif p.duplicate_of:
                status_text += f"  ·  {os.path.basename(p.duplicate_of)}"
            st_item = QTableWidgetItem(status_text)
            color = _STATUS_COLORS.get(p.status)
            if color:
                st_item.setForeground(_brush(color))
            self.table.setItem(row, 6, st_item)

        self.table.blockSignals(False)
        for col in (0, 1, 2, 3):
            self.table.resizeColumnToContents(col)
        self._update_summary()

    def _on_item_changed(self, item) -> None:
        if item.column() != 0:
            return
        plan = item.data(Qt.UserRole)
        if isinstance(plan, ImportPlan):
            checked = item.checkState() == Qt.Checked
            plan.enabled = checked
            plan.delete_source_if_dup = plan.status.is_duplicate and checked
            self._update_summary()

    def _set_all(self, value: bool) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if not (it.flags() & Qt.ItemIsUserCheckable):
                continue
            plan = it.data(Qt.UserRole)
            if not isinstance(plan, ImportPlan):
                continue
            # "Marcar todos" only turns on the safe action (import new files);
            # deleting duplicates from the source stays an explicit per-row tick.
            turn_on = value and plan.status.is_new
            it.setCheckState(Qt.Checked if turn_on else Qt.Unchecked)
            plan.enabled = turn_on
            plan.delete_source_if_dup = plan.status.is_duplicate and turn_on
        self.table.blockSignals(False)
        self._update_summary()

    def _update_summary(self) -> None:
        new = [p for p in self._plans if p.status.is_new]
        will_move = [p for p in new if p.enabled]
        dups = [p for p in self._plans if p.status.is_duplicate]
        will_trash = [p for p in dups if p.delete_source_if_dup]
        errors = [p for p in self._plans if p.status is ImportStatus.ERROR]
        no_loc = [p for p in new if p.location_source is LocationSource.NONE]
        move_bytes = sum(p.size for p in will_move)

        parts = [f"{len(will_move)} para mover ({human_size(move_bytes)})"]
        if dups:
            parts.append(f"{len(dups)} ya existen")
        if will_trash:
            parts.append(f"{len(will_trash)} a borrar del origen")
        if no_loc:
            parts.append(f"{len(no_loc)} sin ubicación")
        if errors:
            parts.append(f"{len(errors)} con error")
        self.summary.setText(" · ".join(parts) if self._plans else "")
        self.move_btn.setEnabled(bool(will_move or will_trash) and not self.busy)

    # -- apply --------------------------------------------------------
    def _start_apply(self) -> None:
        if self.busy:
            return
        to_move = [p for p in self._plans if p.will_import]
        to_trash = [p for p in self._plans if p.will_delete_source]
        if not to_move and not to_trash:
            return

        lines = [f"Vas a mover <b>{len(to_move)}</b> archivo(s) a:<br><code>{self._library}</code>"]
        if to_trash:
            lines.append(
                f"<br>Y enviar a la papelera <b>{len(to_trash)}</b> archivo(s) del origen "
                "que ya están en la biblioteca."
            )
        lines.append(
            "<br><br>La operación queda en el historial y podrás deshacer los movimientos."
        )
        box = QMessageBox(self)
        box.setWindowTitle("Confirmar importación")
        box.setIcon(QMessageBox.Question)
        box.setTextFormat(Qt.RichText)
        box.setText("".join(lines))
        ok = box.addButton("Mover", QMessageBox.AcceptRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not ok:
            return

        self._set_running(True)
        self.progress.setRange(0, len(to_move) + len(to_trash))
        self.move_btn.setEnabled(False)
        runner = ImportApplyRunner(list(self._plans), self.history)
        self._apply = runner
        runner.progress.connect(self._on_apply_progress)
        runner.finished.connect(self._on_apply_done)
        runner.failed.connect(self._on_failed)
        runner.start()

    def _on_apply_progress(self, done: int, total: int, name: str) -> None:
        self.progress.setValue(done)
        self.progress_label.setText(f"Moviendo {done}/{total}…  {name}")

    def _on_apply_done(self, report) -> None:
        if self._apply is not None:
            self._apply.wait()
            self._apply = None
        self._set_running(False)
        self.progress.setRange(0, 0)
        self._last_report = report
        moved = len(report.moved)
        trashed = len(report.source_deleted)
        failed = report.failed
        self.undo_btn.setEnabled(moved > 0)

        msg = f"Se movieron {moved} archivo(s) ({human_size(report.moved_bytes)})."
        if trashed:
            msg += f"\nSe enviaron {trashed} al origen a la papelera."
        if report.cancelled:
            msg = "Operación cancelada.\n" + msg
        if failed:
            msg += f"\n\n{len(failed)} no se pudieron procesar:\n"
            msg += "\n".join(f"• {o.error} ({os.path.basename(o.source_path)})" for o in failed[:8])
            QMessageBox.warning(self, "Importación con avisos", msg)
        else:
            QMessageBox.information(self, "Importación finalizada", msg)
        self.status_message.emit(f"{moved} archivo(s) movidos a la biblioteca.")
        if moved or trashed:
            self._start_preview()

    def _undo(self) -> None:
        if self.busy or self._last_report is None:
            return
        n = len(self._last_report.moved)
        if (
            QMessageBox.question(
                self, "Deshacer", f"¿Devolver {n} archivo(s) movidos a su carpeta de origen?"
            )
            != QMessageBox.Yes
        ):
            return
        self._set_running(True)
        self.progress.setRange(0, 0)
        runner = ImportApplyRunner([], self.history, undo_of=self._last_report.moved)
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
            self, "Deshacer", f"Se devolvieron {len(report.moved)} archivo(s) al origen."
        )
        if os.path.isdir(self._source) and os.path.isdir(self._library):
            self._start_preview()

    def shutdown(self) -> None:
        for r in (self._preview, self._apply):
            if r is not None:
                r.cancel()
                r.wait(2000)


def _brush(hex_color: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(hex_color))
