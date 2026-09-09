"""Detailed comparison for *similar* (not identical) images.

Modes:
  * Normal        - one image at a time
  * Lado a lado   - A and B next to each other
  * Diferencia    - a map of where A and B differ (optionally amplified)
  * Superposición - A and B blended, with an opacity slider

Read-only. Nothing is written to disk.
"""

from __future__ import annotations

from PIL import Image, ImageOps
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from app.core.duplicate_groups import FileRecord
from app.core.image_analyzer import difference_image
from app.core.perceptual import hamming, perceptual_hash, similarity_percent
from app.utils.file_utils import human_size

_MAX_EDGE = 1400


def _load_rgb(path: str, max_edge: int = _MAX_EDGE) -> Image.Image | None:
    try:
        with Image.open(path) as img:
            im = ImageOps.exif_transpose(img).convert("RGB")
            if max(im.size) > max_edge:
                im.thumbnail((max_edge, max_edge), Image.LANCZOS)
            return im
    except Exception:
        return None


def _pil_to_pixmap(img: Image.Image) -> QPixmap:
    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, rgba.width, rgba.height, QImage.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimg)


class _Canvas(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(360, 360)
        self.setStyleSheet("border: 1px solid palette(mid); border-radius: 8px;")
        self._pm: QPixmap | None = None

    def show_pixmap(self, pm: QPixmap | None) -> None:
        self._pm = pm
        self._render()

    def _render(self) -> None:
        if self._pm is None or self._pm.isNull():
            self.setText("(no disponible)")
            return
        self.setText("")
        self.setPixmap(self._pm.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._render()


class AdvancedCompareDialog(QDialog):
    def __init__(self, records: list[FileRecord], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Comparar en detalle")
        self.resize(1200, 800)
        self._records = records
        self._img_cache: dict[int, Image.Image | None] = {}

        self.combo_a = QComboBox()
        self.combo_b = QComboBox()
        for i, r in enumerate(records):
            lbl = f"{r.name}  ({human_size(r.size)})"
            self.combo_a.addItem(lbl, i)
            self.combo_b.addItem(lbl, i)
        self.combo_b.setCurrentIndex(1 if len(records) > 1 else 0)
        self.combo_a.currentIndexChanged.connect(self._refresh)
        self.combo_b.currentIndexChanged.connect(self._refresh)

        # mode buttons
        self.mode_group = QButtonGroup(self)
        mode_row = QHBoxLayout()
        self._modes = {}
        for key, text in [
            ("normal", "Normal"),
            ("side", "Lado a lado"),
            ("diff", "Diferencia"),
            ("overlay", "Superposición"),
        ]:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _=False, k=key: self._set_mode(k))
            self.mode_group.addButton(btn)
            self._modes[key] = btn
            mode_row.addWidget(btn)
        mode_row.addStretch(1)
        self._modes["side"].setChecked(True)
        self._mode = "side"

        self.amplify = QCheckBox("Amplificar diferencias")
        self.amplify.setChecked(True)
        self.amplify.toggled.connect(self._refresh)
        mode_row.addWidget(self.amplify)

        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(50)
        self.opacity.valueChanged.connect(self._refresh)
        self.opacity_label = QLabel("A 50% / B 50%")
        self.opacity_row = QWidget()
        orow = QHBoxLayout(self.opacity_row)
        orow.setContentsMargins(0, 0, 0, 0)
        orow.addWidget(QLabel("A"))
        orow.addWidget(self.opacity, 1)
        orow.addWidget(QLabel("B"))
        orow.addWidget(self.opacity_label)

        top = QHBoxLayout()
        top.addWidget(QLabel("A:"))
        top.addWidget(self.combo_a, 1)
        top.addWidget(QLabel("B:"))
        top.addWidget(self.combo_b, 1)

        self.canvas_a = _Canvas()
        self.canvas_b = _Canvas()
        self.canvas_row = QHBoxLayout()
        self.canvas_row.addWidget(self.canvas_a)
        self.canvas_row.addWidget(self.canvas_b)

        self.readout = QLabel("")
        self.readout.setObjectName("Hint")
        self.readout.setWordWrap(True)

        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.readout, 1)
        bottom.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(mode_row)
        layout.addWidget(self.opacity_row)
        layout.addLayout(self.canvas_row, 1)
        layout.addLayout(bottom)

        self._set_mode("side")

    # ------------------------------------------------------------------
    def _img(self, index: int) -> Image.Image | None:
        if index not in self._img_cache:
            self._img_cache[index] = _load_rgb(self._records[index].path)
        return self._img_cache[index]

    def _set_mode(self, key: str) -> None:
        self._mode = key
        self._modes[key].setChecked(True)
        self.canvas_b.setVisible(key == "side")
        self.opacity_row.setVisible(key == "overlay")
        self.amplify.setVisible(key == "diff")
        self._refresh()

    def _refresh(self) -> None:
        ia = self.combo_a.currentData()
        ib = self.combo_b.currentData()
        if ia is None or ib is None:
            return
        a, b = self._img(ia), self._img(ib)
        ra, rb = self._records[ia], self._records[ib]

        if self._mode == "normal":
            self.canvas_a.show_pixmap(_pil_to_pixmap(a) if a else None)
        elif self._mode == "side":
            self.canvas_a.show_pixmap(_pil_to_pixmap(a) if a else None)
            self.canvas_b.show_pixmap(_pil_to_pixmap(b) if b else None)
        elif self._mode == "diff":
            if ia == ib or a is None or b is None:
                self.canvas_a.show_pixmap(None)
            else:
                diff = difference_image(ra.path, rb.path, amplify=self.amplify.isChecked())
                self.canvas_a.show_pixmap(_pil_to_pixmap(diff) if diff else None)
        elif self._mode == "overlay":
            self._render_overlay(a, b)

        self._update_readout(ra, rb, ia == ib)

    def _render_overlay(self, a: Image.Image | None, b: Image.Image | None) -> None:
        if a is None or b is None:
            self.canvas_a.show_pixmap(_pil_to_pixmap(a or b) if (a or b) else None)
            return
        if a.size != b.size:
            b = b.resize(a.size, Image.LANCZOS)
        alpha = self.opacity.value() / 100.0  # 0 -> all A, 1 -> all B
        blended = Image.blend(a, b, alpha)
        self.opacity_label.setText(f"A {100 - self.opacity.value()}% / B {self.opacity.value()}%")
        self.canvas_a.show_pixmap(_pil_to_pixmap(blended))

    def _update_readout(self, ra: FileRecord, rb: FileRecord, same: bool) -> None:
        lines = [f"A: {ra.name} — {human_size(ra.size)}"]
        resa, resb = ra.resolution, rb.resolution
        if resa:
            lines[0] += f" — {resa[0]}×{resa[1]}"
        if same:
            lines.append("A y B son el mismo archivo.")
        else:
            lines.append(
                f"B: {rb.name} — {human_size(rb.size)}"
                + (f" — {resb[0]}×{resb[1]}" if resb else "")
            )
            ha, hb = perceptual_hash(ra.path), perceptual_hash(rb.path)
            if ha is not None and hb is not None:
                d = hamming(ha, hb)
                lines.append(
                    f"Similitud perceptual ≈ {similarity_percent(d):.1f}% (distancia {d}/64)"
                )
        self.readout.setText("\n".join(lines))
