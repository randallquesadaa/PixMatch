"""Full-resolution image viewer with zoom / pan / fit.

Can hold a whole group: navigate ◀ ▶ between the images while zoomed in, and
take the keep / delete decision right there. The full image is loaded only
when shown and released when the dialog closes.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImageReader, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from app.core.duplicate_groups import Decision, FileRecord
from app.utils.file_utils import human_size, open_in_file_manager, open_with_default_app


class _ZoomView(QGraphicsView):
    def __init__(self) -> None:
        super().__init__()
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHints(self.renderHints())

    def wheelEvent(self, event) -> None:
        self.scale(*(1.25, 1.25) if event.angleDelta().y() > 0 else (0.8, 0.8))

    def reset_zoom(self) -> None:
        self.resetTransform()


class ImageViewerDialog(QDialog):
    decision_changed = Signal()

    def __init__(self, records, index: int = 0, parent=None) -> None:
        super().__init__(parent)
        if isinstance(records, (str, FileRecord)):
            records = [records]
        self._records = list(records)
        self._index = max(0, min(index, len(self._records) - 1))
        self.pending_delete: FileRecord | None = None
        self.resize(1180, 820)

        self._scene = QGraphicsScene(self)
        self._view = _ZoomView()
        self._view.setScene(self._scene)
        self._item: QGraphicsPixmapItem | None = None

        self._info = QLabel("")
        self._info.setObjectName("Hint")
        self._info.setWordWrap(True)

        fit_btn = QPushButton("Ajustar a ventana")
        orig_btn = QPushButton("Tamaño original")
        zout_btn = QPushButton("−")
        zin_btn = QPushButton("+")
        open_btn = QPushButton("Abrir con app del sistema")
        folder_btn = QPushButton("Abrir ubicación")
        fit_btn.clicked.connect(self.fit)
        orig_btn.clicked.connect(self._view.reset_zoom)
        zin_btn.clicked.connect(lambda: self._view.scale(1.25, 1.25))
        zout_btn.clicked.connect(lambda: self._view.scale(0.8, 0.8))
        open_btn.clicked.connect(lambda: open_with_default_app(self._current().path))
        folder_btn.clicked.connect(lambda: open_in_file_manager(self._current().path))

        top = QHBoxLayout()
        for w in (fit_btn, orig_btn, zout_btn, zin_btn, open_btn, folder_btn):
            top.addWidget(w)
        top.addStretch(1)

        self._prev_btn = QPushButton("◀")
        self._next_btn = QPushButton("▶")
        self._counter = QLabel("")
        self._prev_btn.clicked.connect(lambda: self._go(-1))
        self._next_btn.clicked.connect(lambda: self._go(1))

        self._keep_btn = QPushButton("✔ Mantener")
        self._mark_btn = QPushButton("Marcar para eliminar")
        self._del_btn = QPushButton("🗑 Eliminar ahora…")
        self._del_btn.setObjectName("danger")
        self._keep_btn.clicked.connect(lambda: self._set_decision(Decision.KEEP))
        self._mark_btn.clicked.connect(lambda: self._set_decision(Decision.DELETE))
        self._del_btn.clicked.connect(self._request_delete)

        bottom = QHBoxLayout()
        bottom.addWidget(self._prev_btn)
        bottom.addWidget(self._counter)
        bottom.addWidget(self._next_btn)
        bottom.addStretch(1)
        bottom.addWidget(self._keep_btn)
        bottom.addWidget(self._mark_btn)
        bottom.addWidget(self._del_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._view, 1)
        layout.addWidget(self._info)
        layout.addLayout(bottom)

        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close)
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self._go(-1))
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self._go(1))
        QShortcut(QKeySequence("F"), self, self.fit)

        self._show_current()

    # ------------------------------------------------------------------
    def _current(self) -> FileRecord:
        return self._records[self._index]

    def _go(self, delta: int) -> None:
        new = self._index + delta
        if 0 <= new < len(self._records):
            self._index = new
            self._show_current()

    def _request_delete(self) -> None:
        # hand back to the caller, which runs the confirmation + deletion
        self.pending_delete = self._current()
        self.accept()

    def _set_decision(self, decision: Decision) -> None:
        rec = self._current()
        rec.decision = decision
        rec.reviewed = True
        self.decision_changed.emit()
        self._refresh_decision_row()

    def _show_current(self) -> None:
        rec = self._current()
        self.setWindowTitle(f"{rec.name}  —  {self._index + 1}/{len(self._records)}")
        self._counter.setText(f"{self._index + 1} / {len(self._records)}")
        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < len(self._records) - 1)

        self._scene.clear()
        self._item = None
        reader = QImageReader(rec.path)
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            self._info.setText(f"{rec.path}\nNo se pudo cargar la imagen.")
        else:
            pm = QPixmap.fromImage(image)
            self._item = self._scene.addPixmap(pm)
            self._scene.setSceneRect(pm.rect())
            try:
                fsize = human_size(os.path.getsize(rec.path))
            except OSError:
                fsize = "-"
            extra = ""
            if rec.similarity_percent is not None:
                extra = f"   ·   similitud {rec.similarity_percent:.1f}%"
            self._info.setText(f"{pm.width()} × {pm.height()} px   ·   {fsize}{extra}\n{rec.path}")
            self.fit()
        self._refresh_decision_row()

    def _refresh_decision_row(self) -> None:
        rec = self._current()
        gone = rec.deleted or not os.path.exists(rec.path)
        self._keep_btn.setEnabled(not gone)
        self._mark_btn.setEnabled(not gone)
        self._del_btn.setEnabled(not gone)
        if rec.deleted:
            self._del_btn.setText("Eliminado")
        elif rec.decision is Decision.KEEP:
            self._keep_btn.setText("✔ Se conservará")
            self._mark_btn.setText("Marcar para eliminar")
        elif rec.decision is Decision.DELETE:
            self._keep_btn.setText("✔ Mantener")
            self._mark_btn.setText("● Marcado para eliminar")
        else:
            self._keep_btn.setText("✔ Mantener")
            self._mark_btn.setText("Marcar para eliminar")

    def fit(self) -> None:
        if self._item is not None:
            self._view.reset_zoom()
            self._view.fitInView(self._item, Qt.KeepAspectRatio)
