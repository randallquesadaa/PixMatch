"""Side-by-side comparison for a group of videos: a representative frame and
the key metadata for each, plus the estimated content similarity.

Read-only. Playback is delegated to the OS default player.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core.duplicate_groups import FileRecord
from app.core.video_analyzer import compare_frame_hashes
from app.ui.widgets.thumbnail_loader import request_thumbnail
from app.utils.file_utils import human_size, open_in_file_manager, open_with_default_app

_FRAME_EDGE = 360


def _duration_str(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class _VideoColumn(QWidget):
    def __init__(self, record: FileRecord, similarity: float | None, parent=None) -> None:
        super().__init__(parent)
        self.record = record

        self.frame = QLabel("Cargando fotograma…")
        self.frame.setObjectName("Hint")
        self.frame.setAlignment(Qt.AlignCenter)
        self.frame.setFixedSize(_FRAME_EDGE, int(_FRAME_EDGE * 0.62))
        self.frame.setStyleSheet("border: 1px solid palette(mid); border-radius: 8px;")

        name = QLabel(f"<b>{record.name}</b>")
        name.setWordWrap(True)
        name.setTextInteractionFlags(Qt.TextSelectableByMouse)

        v = record.video_info
        rows = [f"<b>Tamaño:</b> {human_size(record.size)}"]
        if v is not None and not getattr(v, "error", None):
            rows += [
                f"<b>Duración:</b> {_duration_str(v.duration)}",
                f"<b>Resolución:</b> {v.width} × {v.height}",
                f"<b>Códec:</b> {v.video_codec or '-'}   <b>FPS:</b> {v.fps or '-'}",
                f"<b>Bitrate:</b> {v.bitrate // 1000 if v.bitrate else '-'} kb/s",
                f"<b>Audio:</b> {v.audio_codec if v.has_audio else 'sin audio'}",
            ]
        elif v is not None and v.error:
            rows.append(f"<b>Vídeo:</b> {v.error}")
        if similarity is not None:
            rows.append(f"<b>Similitud estimada vs. referencia:</b> {similarity:.1f}%")
        meta = QLabel("<br>".join(rows))
        meta.setWordWrap(True)
        meta.setTextInteractionFlags(Qt.TextSelectableByMouse)

        play = QPushButton("▶ Reproducir")
        folder = QPushButton("Abrir carpeta")
        play.clicked.connect(lambda: open_with_default_app(record.path))
        folder.clicked.connect(lambda: open_in_file_manager(record.path))
        btns = QHBoxLayout()
        btns.addWidget(play)
        btns.addWidget(folder)
        btns.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self.frame, 0, Qt.AlignCenter)
        layout.addWidget(name)
        layout.addWidget(meta)
        layout.addLayout(btns)
        layout.addStretch(1)

        request_thumbnail(record.path, record.size, record.mtime, _FRAME_EDGE, self._on_frame)

    def _on_frame(self, path: str, pixmap: object) -> None:
        if path != self.record.path:
            return
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self.frame.setText("")
            self.frame.setPixmap(pixmap)
        else:
            self.frame.setText("Sin fotograma\n(¿FFmpeg disponible?)")


class VideoCompareDialog(QDialog):
    def __init__(self, records: list[FileRecord], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Comparar vídeos")
        self.resize(min(1200, 40 + 400 * len(records)), 720)

        anchor = max(records, key=lambda r: r.size)

        container = QWidget()
        grid = QGridLayout(container)
        for col, rec in enumerate(records):
            if rec is anchor:
                sim: float | None = 100.0
            else:
                sim = compare_frame_hashes(rec.frame_hashes or [], anchor.frame_hashes or [])
            grid.addWidget(_VideoColumn(rec, sim), 0, col)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)

        note = QLabel(
            "La similitud se estima comparando unos pocos fotogramas. Un valor "
            "alto indica «mismo contenido probable», nunca una certeza del 100%."
        )
        note.setObjectName("Hint")
        note.setWordWrap(True)

        close = QPushButton("Cerrar")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(note, 1)
        bottom.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addLayout(bottom)
