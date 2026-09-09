"""Confirmation dialog before any deletion, plus the operation-history viewer."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.core.deletion_manager import DeletionMode, DeletionPreview
from app.database.history import OperationHistory
from app.utils.file_utils import human_size


class DeletionConfirmDialog(QDialog):
    """Returns the chosen :class:`DeletionMode` via :attr:`chosen_mode` when
    accepted, or ``None`` when cancelled."""

    def __init__(self, preview: DeletionPreview, default_mode: DeletionMode, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("⚠️  Confirmar eliminación")
        self.setModal(True)
        self.resize(720, 560)
        self._preview = preview
        self.chosen_mode: DeletionMode | None = None

        n = preview.count
        headline = QLabel()
        headline.setWordWrap(True)
        headline.setStyleSheet("font-size: 15px; font-weight: 600;")
        if n == 1:
            headline.setText(f"Vas a eliminar 1 archivo y liberar {human_size(preview.total_bytes)}.")
        else:
            headline.setText(
                f"Vas a eliminar {n} archivos y liberar aproximadamente "
                f"{human_size(preview.total_bytes)}."
            )

        self.mode_trash = QCheckBox("Enviar a la papelera del sistema (recomendado, recuperable)")
        self.mode_trash.setChecked(default_mode is DeletionMode.TRASH)
        self.mode_trash.toggled.connect(self._sync_mode_labels)
        self._perm_note = QLabel(
            "⚠️ Con la papelera desactivada, los archivos se eliminan de forma "
            "PERMANENTE. Esta acción no se puede deshacer."
        )
        self._perm_note.setWordWrap(True)
        self._perm_note.setObjectName("Warn")

        # file list
        table = QTableWidget(n, 2)
        table.setHorizontalHeaderLabels(["Archivo", "Tamaño"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        for row, item in enumerate(preview.items):
            table.setItem(row, 0, QTableWidgetItem(item.record.path))
            size_item = QTableWidgetItem(human_size(item.record.size))
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(row, 1, size_item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(False)
        table.setColumnWidth(0, 520)

        layout = QVBoxLayout(self)
        layout.addWidget(headline)
        layout.addWidget(self.mode_trash)
        layout.addWidget(self._perm_note)

        # warnings
        self._ack_empty: QCheckBox | None = None
        if preview.warnings or preview.groups_left_empty:
            warn = QPlainTextEdit()
            warn.setReadOnly(True)
            warn.setMaximumHeight(140)
            lines = []
            for gid in preview.groups_left_empty:
                lines.append(f"• El grupo #{gid} quedaría SIN ninguna copia.")
            for w in preview.warnings:
                lines.append(f"• {w.message}  ({w.path})")
            warn.setPlainText("\n".join(lines))
            layout.addWidget(QLabel("Avisos:"))
            layout.addWidget(warn)

            if preview.groups_left_empty:
                self._ack_empty = QCheckBox(
                    f"Entiendo que {len(preview.groups_left_empty)} grupo(s) quedarán "
                    f"sin ninguna copia."
                )
                self._ack_empty.toggled.connect(self._update_ok)
                layout.addWidget(self._ack_empty)

        layout.addWidget(QLabel("Archivos que se eliminarán:"))
        layout.addWidget(table, 1)

        self._buttons = QDialogButtonBox()
        self._cancel = self._buttons.addButton("Cancelar", QDialogButtonBox.RejectRole)
        self._confirm = self._buttons.addButton("Confirmar eliminación", QDialogButtonBox.AcceptRole)
        self._confirm.setObjectName("danger")
        self._cancel.setDefault(True)
        self._buttons.accepted.connect(self._accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._sync_mode_labels()
        self._update_ok()

    def _sync_mode_labels(self) -> None:
        permanent = not self.mode_trash.isChecked()
        self._perm_note.setVisible(permanent)
        self._confirm.setText(
            "Eliminar permanentemente" if permanent else "Enviar a la papelera"
        )

    def _update_ok(self) -> None:
        ok = True
        if self._ack_empty is not None and not self._ack_empty.isChecked():
            ok = False
        self._confirm.setEnabled(ok)

    def _accept(self) -> None:
        self.chosen_mode = (
            DeletionMode.TRASH if self.mode_trash.isChecked() else DeletionMode.PERMANENT
        )
        self.accept()


class HistoryDialog(QDialog):
    def __init__(self, history: OperationHistory, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Historial de operaciones")
        self.resize(900, 560)
        self._history = history

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Fecha", "Acción", "Resultado", "Archivo", "Detalle"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        refresh = QPushButton("Actualizar")
        clear = QPushButton("Vaciar historial…")
        refresh.clicked.connect(self._load)
        clear.clicked.connect(self._clear)
        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)

        bar = QHBoxLayout()
        bar.addWidget(refresh)
        bar.addWidget(clear)
        bar.addStretch(1)
        bar.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addLayout(bar)
        self._load()

    def _load(self) -> None:
        rows = self._history.recent(1000)
        self.table.setRowCount(len(rows))
        action_es = {"trash": "papelera", "permanent": "eliminación permanente",
                     "rename": "renombrado"}
        for i, e in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(e.ts))
            self.table.setItem(i, 1, QTableWidgetItem(action_es.get(e.action, e.action)))
            res = QTableWidgetItem("OK" if e.result == "ok" else "ERROR")
            self.table.setItem(i, 2, res)
            self.table.setItem(i, 3, QTableWidgetItem(e.path))
            self.table.setItem(i, 4, QTableWidgetItem(e.detail or ""))
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(3, 420)

    def _clear(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if QMessageBox.question(
            self, "Vaciar historial",
            "¿Borrar todo el historial de operaciones? Esto no afecta a tus archivos.",
        ) == QMessageBox.Yes:
            self._history.clear()
            self._load()
