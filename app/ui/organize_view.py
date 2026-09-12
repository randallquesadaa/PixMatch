"""The "Organizar biblioteca" tab.

Rearranges an already-existing, disorganised folder (an old hard drive, a
photo dump) into the same ``<country>/<YYYY>/<YYYY-MM>/<date-name>`` layout
the import tool uses -- in place, on that same folder. Nothing is compared or
evaluated: a file either moves to where it belongs, or (already there) is
left alone. Every move is verified, logged and undoable.
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
from app.core.importer import LocationSource
from app.core.organizer import OrganizePlan, OrganizeStatus
from app.core.renamer import PATTERN_PRESETS
from app.database.history import OperationHistory
from app.utils.file_utils import human_size
from app.utils.logging_setup import get_logger
from app.workers.organize_worker import OrganizeApplyRunner, OrganizePreviewRunner

log = get_logger(__name__)

_STATUS_COLORS = {
    OrganizeStatus.MOVE: "#2e7d32",
    OrganizeStatus.MOVE_SUFFIXED: "#b26a00",
    OrganizeStatus.UNCHANGED: "#607d8b",
    OrganizeStatus.ERROR: "#c62828",
}


class OrganizeView(QWidget):
    status_message = Signal(str)

    def __init__(self, config: AppConfig, history: OperationHistory, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.history = history
        self._root = config.organize_root_folder or ""
        self._plans: list[OrganizePlan] = []
        self._preview: OrganizePreviewRunner | None = None
        self._apply: OrganizeApplyRunner | None = None
        self._last_report = None

        self.root_edit = QLineEdit(self._root)
        self.root_edit.setReadOnly(True)
        self.root_edit.setPlaceholderText("Carpeta o disco a organizar")
        root_btn = QPushButton("📁  Carpeta")
        root_btn.clicked.connect(self._choose_root)

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
        row1.addWidget(QLabel("Carpeta:"))
        row1.addWidget(self.root_edit, 1)
        row1.addWidget(root_btn)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Formato:"))
        row2.addWidget(self.pattern_combo, 1)
        row2.addWidget(self.videos_check)
        row2.addWidget(QLabel("Sin ubicación:"))
        row2.addWidget(self.no_loc_edit)
        row3 = QHBoxLayout()
        row3.addWidget(self.scan_btn)
        row3.addWidget(self.cancel_btn)
        row3.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("Hint")

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["", "Archivo", "Fecha", "País", "Carpeta destino", "Estado"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)

        self.only_pending = QCheckBox("Mostrar solo los que se moverán")
        self.only_pending.setChecked(False)
        self.only_pending.toggled.connect(self._repopulate_table)
        self.select_all_btn = QPushButton("Marcar todos")
        self.select_none_btn = QPushButton("Desmarcar todos")
        self.select_all_btn.clicked.connect(lambda: self._set_all(True))
        self.select_none_btn.clicked.connect(lambda: self._set_all(False))

        table_bar = QHBoxLayout()
        table_bar.addWidget(self.only_pending)
        table_bar.addStretch(1)
        table_bar.addWidget(self.select_all_btn)
        table_bar.addWidget(self.select_none_btn)

        self.summary = QLabel("")
        self.summary.setStyleSheet("font-weight: 600;")
        self.move_btn = QPushButton("🗂️  Reorganizar")
        self.move_btn.setObjectName("primary")
        self.move_btn.setEnabled(False)
        self.move_btn.clicked.connect(self._start_apply)
        self.undo_btn = QPushButton("↩  Deshacer última reorganización")
        self.undo_btn.setEnabled(False)
        self.undo_btn.clicked.connect(self._undo)

        bottom = QHBoxLayout()
        bottom.addWidget(self.summary, 1)
        bottom.addWidget(self.undo_btn)
        bottom.addWidget(self.move_btn)

        note = QLabel(
            "Reordena <b>una sola carpeta</b> (un disco viejo, un volcado sin orden) con la "
            "misma estructura que usa «Importar»: <code>&lt;País&gt;/&lt;Año&gt;/&lt;Año-Mes&gt;/</code>. "
            "No compara ni evalúa nada — no hay detección de duplicados; cada archivo simplemente "
            "se coloca en su sitio, o se deja igual si ya está ahí. El movimiento es "
            "atómico dentro del mismo disco (o copiar → verificar → borrar si cruza de disco); "
            "nunca sobrescribe. Todo queda en el historial y se puede deshacer."
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

        self._refresh_scan_enabled()

    # ==================================================================
    def _choose_root(self) -> None:
        start = self._root or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, "Carpeta a organizar", start)
        if folder:
            self._root = folder
            self.root_edit.setText(folder)
            self.config.organize_root_folder = folder
            self.config.save()
            self._refresh_scan_enabled()

    def _refresh_scan_enabled(self) -> None:
        ok = bool(self._root) and os.path.isdir(self._root)
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
        if self.busy or not os.path.isdir(self._root):
            return
        self._persist_options()
        self._plans = []
        self.table.setRowCount(0)
        self.move_btn.setEnabled(False)
        self._set_running(True)

        runner = OrganizePreviewRunner(self._root, self.config)
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
        self.scan_btn.setEnabled(not running and bool(self._root) and os.path.isdir(self._root))
        if not running:
            self.progress_label.setText("")

    def _cancel(self) -> None:
        for r in (self._preview, self._apply):
            if r is not None:
                r.cancel()

    # -- table --------------------------------------------------------
    def _visible_plans(self) -> list[OrganizePlan]:
        if self.only_pending.isChecked():
            return [p for p in self._plans if p.status.will_move_by_default]
        return list(self._plans)

    def _repopulate_table(self) -> None:
        plans = self._visible_plans()
        self.table.blockSignals(True)
        self.table.setRowCount(len(plans))
        for row, p in enumerate(plans):
            chk = QTableWidgetItem()
            if p.status.will_move_by_default:
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Checked if p.enabled else Qt.Unchecked)
                chk.setToolTip("Mover a su sitio")
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

            rel = os.path.relpath(p.dest_path, self._root) if p.dest_dir else "—"
            self.table.setItem(row, 4, QTableWidgetItem(rel))

            status_text = p.status.label
            if p.status is OrganizeStatus.ERROR and p.error:
                status_text = p.error
            st_item = QTableWidgetItem(status_text)
            color = _STATUS_COLORS.get(p.status)
            if color:
                st_item.setForeground(_brush(color))
            self.table.setItem(row, 5, st_item)

        self.table.blockSignals(False)
        for col in (0, 1, 2, 3):
            self.table.resizeColumnToContents(col)
        self._update_summary()

    def _on_item_changed(self, item) -> None:
        if item.column() != 0:
            return
        plan = item.data(Qt.UserRole)
        if isinstance(plan, OrganizePlan):
            plan.enabled = item.checkState() == Qt.Checked
            self._update_summary()

    def _set_all(self, value: bool) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if not (it.flags() & Qt.ItemIsUserCheckable):
                continue
            plan = it.data(Qt.UserRole)
            if not isinstance(plan, OrganizePlan):
                continue
            it.setCheckState(Qt.Checked if value else Qt.Unchecked)
            plan.enabled = value
        self.table.blockSignals(False)
        self._update_summary()

    def _update_summary(self) -> None:
        pending = [p for p in self._plans if p.status.will_move_by_default]
        will_move = [p for p in pending if p.enabled]
        unchanged = [p for p in self._plans if p.status is OrganizeStatus.UNCHANGED]
        errors = [p for p in self._plans if p.status is OrganizeStatus.ERROR]
        no_loc = [p for p in self._plans if p.location_source is LocationSource.NONE]
        move_bytes = sum(p.size for p in will_move)

        parts = [f"{len(will_move)} para mover ({human_size(move_bytes)})"]
        if unchanged:
            parts.append(f"{len(unchanged)} ya en su sitio")
        if no_loc:
            parts.append(f"{len(no_loc)} sin ubicación")
        if errors:
            parts.append(f"{len(errors)} con error")
        self.summary.setText(" · ".join(parts) if self._plans else "")
        self.move_btn.setEnabled(bool(will_move) and not self.busy)

    # -- apply --------------------------------------------------------
    def _start_apply(self) -> None:
        if self.busy:
            return
        to_move = [p for p in self._plans if p.will_move]
        if not to_move:
            return

        box = QMessageBox(self)
        box.setWindowTitle("Confirmar reorganización")
        box.setIcon(QMessageBox.Question)
        box.setTextFormat(Qt.RichText)
        box.setText(
            f"Vas a reordenar <b>{len(to_move)}</b> archivo(s) dentro de:<br>"
            f"<code>{self._root}</code><br><br>"
            "La operación queda en el historial y podrás deshacer los movimientos."
        )
        ok = box.addButton("Reorganizar", QMessageBox.AcceptRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not ok:
            return

        self._set_running(True)
        self.progress.setRange(0, len(to_move))
        self.move_btn.setEnabled(False)
        runner = OrganizeApplyRunner(list(self._plans), self.history)
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
        failed = report.failed
        self.undo_btn.setEnabled(moved > 0)

        msg = f"Se reordenaron {moved} archivo(s) ({human_size(report.moved_bytes)})."
        if report.cancelled:
            msg = "Operación cancelada.\n" + msg
        if failed:
            msg += f"\n\n{len(failed)} no se pudieron procesar:\n"
            msg += "\n".join(f"• {o.error} ({os.path.basename(o.source_path)})" for o in failed[:8])
            QMessageBox.warning(self, "Reorganización con avisos", msg)
        else:
            QMessageBox.information(self, "Reorganización finalizada", msg)
        self.status_message.emit(f"{moved} archivo(s) reordenados.")
        if moved:
            self._start_preview()

    def _undo(self) -> None:
        if self.busy or self._last_report is None:
            return
        n = len(self._last_report.moved)
        if (
            QMessageBox.question(
                self, "Deshacer", f"¿Devolver {n} archivo(s) a su ubicación original?"
            )
            != QMessageBox.Yes
        ):
            return
        self._set_running(True)
        self.progress.setRange(0, 0)
        runner = OrganizeApplyRunner([], self.history, undo_of=self._last_report.moved)
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
            self, "Deshacer", f"Se devolvieron {len(report.moved)} archivo(s) a su lugar original."
        )
        if os.path.isdir(self._root):
            self._start_preview()

    def shutdown(self) -> None:
        for r in (self._preview, self._apply):
            if r is not None:
                r.cancel()
                r.wait(2000)


def _brush(hex_color: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(hex_color))
