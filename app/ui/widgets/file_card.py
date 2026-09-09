"""A single file inside a duplicate group: thumbnail, details, keep-checkbox
and per-file actions."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from app.core.duplicate_groups import Decision, FileRecord
from app.core.metadata import file_timestamps
from app.ui.widgets.thumbnail_loader import request_thumbnail
from app.utils.file_utils import human_size, open_in_file_manager, open_with_default_app

_THUMB_EDGE = 260


class FileCard(QFrame):
    decision_changed = Signal()
    zoom_requested = Signal(object)  # FileRecord - open the full-res viewer
    delete_requested = Signal(object)  # FileRecord - delete this single file now

    def __init__(self, record: FileRecord, recommended: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.record = record
        self._recommended = recommended
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

        self._thumb = QLabel("Cargando…")
        self._thumb.setAlignment(Qt.AlignCenter)
        self._thumb.setFixedSize(_THUMB_EDGE, _THUMB_EDGE)
        self._thumb.setObjectName("Hint")
        self._thumb.setCursor(Qt.PointingHandCursor)
        self._thumb.setToolTip("Doble clic para ampliar")
        self._thumb.mouseDoubleClickEvent = lambda _e: self._request_zoom()  # type: ignore[assignment]

        self.keep_checkbox = QCheckBox("Mantener este archivo")
        self.keep_checkbox.setChecked(record.decision is Decision.KEEP)
        self.keep_checkbox.toggled.connect(self._on_keep_toggled)

        self._name = QLabel()
        self._name.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._name.setWordWrap(True)
        self._name.setStyleSheet("font-weight: 600;")

        self._details = QLabel()
        self._details.setObjectName("Hint")
        self._details.setWordWrap(True)
        self._details.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._status = QLabel()
        self._status.setStyleSheet("font-weight: 600;")

        is_image = record.kind.value == "image"
        self.zoom_btn = QPushButton("🔍 Ampliar" if is_image else "▶ Reproducir")
        open_btn = QPushButton("Abrir archivo")
        folder_btn = QPushButton("Abrir carpeta")
        copy_btn = QPushButton("Copiar ruta")
        self.delete_btn = QPushButton("🗑 Eliminar")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.setToolTip("Eliminar solo este archivo (con confirmación)")
        self.zoom_btn.clicked.connect(self._request_zoom)
        open_btn.clicked.connect(lambda: open_with_default_app(self.record.path))
        folder_btn.clicked.connect(lambda: open_in_file_manager(self.record.path))
        copy_btn.clicked.connect(self._copy_path)
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.record))

        actions = QHBoxLayout()
        actions.setSpacing(6)
        for b in (self.zoom_btn, open_btn, folder_btn, copy_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        actions.addWidget(self.delete_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        top = QHBoxLayout()
        top.addWidget(self._thumb, 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.addWidget(self._name)
        info.addWidget(self._status)
        info.addWidget(self._details, 1)
        info.addWidget(self.keep_checkbox)
        top.addLayout(info, 1)
        layout.addLayout(top)
        layout.addLayout(actions)

        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)

        self._populate_text()
        self._load_thumb()
        self.refresh_style()

    # ------------------------------------------------------------------
    def _load_thumb(self) -> None:
        request_thumbnail(
            self.record.path,
            self.record.size,
            self.record.mtime,
            _THUMB_EDGE - 8,
            self._on_thumb,
        )

    def _on_thumb(self, path: str, pixmap: object) -> None:
        if path != self.record.path:
            return
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self._thumb.setPixmap(pixmap)
            self._thumb.setText("")
        else:
            self._thumb.setText("Sin vista previa")

    def _populate_text(self) -> None:
        rec = self.record
        self._name.setText(rec.name + ("   ⭐ recomendado" if self._recommended else ""))

        created, modified = file_timestamps(rec.path)
        lines = [
            f"<b>Ruta:</b> {rec.path}",
            f"<b>Tamaño:</b> {human_size(rec.size)}",
        ]
        if rec.kind.value == "image":
            info = rec.image_info()
            if info.error:
                lines.append(f"<b>Imagen:</b> {info.error}")
            else:
                w, h = info.oriented_size
                lines.append(f"<b>Resolución:</b> {w} × {h}  ({info.megapixels:.1f} MP)")
                lines.append(
                    f"<b>Formato:</b> {info.format or '-'}   <b>Modo:</b> {info.mode or '-'}"
                )
                if info.orientation != 1:
                    lines.append(
                        f"<b>Orientación EXIF:</b> {info.orientation} (normalizada en la vista)"
                    )
                if info.camera_make or info.camera_model:
                    lines.append(f"<b>Cámara:</b> {info.camera_make} {info.camera_model}".strip())
                if info.date_taken:
                    lines.append(f"<b>Fecha tomada:</b> {info.date_taken}")
        elif rec.kind.value == "video":
            v = rec.video_info
            if v is None:
                lines.append("<b>Vídeo:</b> sin analizar (activa «Analizar vídeos»).")
            elif getattr(v, "error", None):
                lines.append(f"<b>Vídeo:</b> {v.error}")
            else:
                mm, ss = divmod(int(v.duration), 60)
                hh, mm = divmod(mm, 60)
                dur = f"{hh:02d}:{mm:02d}:{ss:02d}"
                lines.append(f"<b>Duración:</b> {dur}   <b>Resolución:</b> {v.width} × {v.height}")
                lines.append(
                    f"<b>Códec:</b> {v.video_codec or '-'}   "
                    f"<b>FPS:</b> {v.fps or '-'}   "
                    f"<b>Bitrate:</b> {v.bitrate // 1000 if v.bitrate else '-'} kb/s"
                )
                lines.append(f"<b>Audio:</b> {v.audio_codec if v.has_audio else 'sin audio'}")
                if v.creation_time:
                    lines.append(f"<b>Creado (metadatos):</b> {v.creation_time}")
                if rec.frame_hashes:
                    got = sum(1 for h in rec.frame_hashes if h is not None)
                    lines.append(f"<b>Fotogramas muestreados:</b> {got}/{len(rec.frame_hashes)}")
        if created:
            lines.append(f"<b>Creado:</b> {created}")
        lines.append(f"<b>Modificado:</b> {modified}")
        if rec.similarity_percent is not None:
            lines.append(
                f"<b>Similitud vs. referencia del grupo:</b> {rec.similarity_percent:.1f}%"
            )
        if rec.sha256:
            lines.append(f"<b>SHA-256:</b> <span style='font-family:monospace'>{rec.sha256}</span>")
        if rec.pixel_digest:
            lines.append(
                "<b>Hash de píxeles:</b> "
                f"<span style='font-family:monospace'>{rec.pixel_digest[:32]}…</span>"
            )
        if rec.phash is not None:
            lines.append(
                f"<b>pHash:</b> <span style='font-family:monospace'>{rec.phash:016x}</span>"
            )
        if rec.error:
            lines.append(f"<b>Aviso:</b> {rec.error}")
        self._details.setText("<br>".join(lines))

    # ------------------------------------------------------------------
    def _on_keep_toggled(self, checked: bool) -> None:
        self.record.decision = Decision.KEEP if checked else Decision.UNDECIDED
        self.record.reviewed = True
        self.refresh_style()
        self.decision_changed.emit()

    def set_decision(self, decision: Decision) -> None:
        self.record.decision = decision
        self.record.reviewed = True
        self.keep_checkbox.blockSignals(True)
        self.keep_checkbox.setChecked(decision is Decision.KEEP)
        self.keep_checkbox.blockSignals(False)
        self.refresh_style()
        self.decision_changed.emit()

    def _set_status(self, text: str, object_name: str, card_border: str = "") -> None:
        self._status.setText(text)
        self._status.setObjectName(object_name)
        self._status.setStyleSheet("")  # let the theme's #… rule apply
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
        self.setStyleSheet(card_border)

    def refresh_style(self) -> None:
        if self.record.deleted:
            self._set_status(
                "🗑 ELIMINADO (movido a la papelera / eliminado)",
                "StatusDelete",
                "#Card { border: 2px solid #dc2626; }",
            )
            self.keep_checkbox.setEnabled(False)
            self.setEnabled(False)
            return
        d = self.record.decision
        if d is Decision.KEEP:
            self._set_status(
                "✔ Se conservará", "StatusKeep", "#Card { border: 2px solid #16a34a; }"
            )
        elif d is Decision.DELETE:
            self._set_status(
                "🗑 Marcado para eliminar (no se borra nada todavía)",
                "StatusDelete",
                "#Card { border: 2px solid #dc2626; }",
            )
        else:
            self._set_status("Sin decidir", "Muted", "")

    # ------------------------------------------------------------------
    def _copy_path(self) -> None:
        QGuiApplication.clipboard().setText(self.record.path)

    def _request_zoom(self) -> None:
        if not os.path.exists(self.record.path):
            return
        if self.record.kind.value == "video":
            open_with_default_app(self.record.path)
            return
        self.zoom_requested.emit(self.record)

    def _context_menu(self, pos) -> None:
        menu = QMenu(self)
        if self.record.kind.value == "video":
            menu.addAction("Reproducir (app del sistema)", self._request_zoom)
        else:
            menu.addAction("🔍 Ampliar", self._request_zoom)
        menu.addAction("Abrir archivo", lambda: open_with_default_app(self.record.path))
        menu.addAction("Abrir carpeta", lambda: open_in_file_manager(self.record.path))
        menu.addAction("Copiar ruta", self._copy_path)
        menu.addSeparator()
        menu.addAction("Mantener este archivo", lambda: self.set_decision(Decision.KEEP))
        menu.addAction("Marcar para eliminar", lambda: self.set_decision(Decision.DELETE))
        menu.addAction("Quitar decisión", lambda: self.set_decision(Decision.UNDECIDED))
        menu.addSeparator()
        act_del = menu.addAction(
            "🗑 Eliminar este archivo…", lambda: self.delete_requested.emit(self.record)
        )
        act_del.setEnabled(not self.record.deleted)
        menu.exec(self.mapToGlobal(pos))
