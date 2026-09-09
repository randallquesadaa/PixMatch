"""Settings dialog. Phase-1 options are live; later-phase options are shown
but marked as such so the user knows what to expect."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config import COMMON_EXCLUDES, AppConfig
from app.utils.logging_setup import LOG_FILE


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configuración")
        self.resize(620, 560)
        self._config = config

        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._scan_tab(), "Escaneo")
        tabs.addTab(self._exclude_tab(), "Exclusiones")
        tabs.addTab(self._logs_tab(), "Logs")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["dark", "light"])
        self.theme_combo.setCurrentText(self._config.theme)
        form.addRow("Tema", self.theme_combo)

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Español", "es")
        self.lang_combo.addItem("English (parcial)", "en")
        i = self.lang_combo.findData(self._config.language)
        self.lang_combo.setCurrentIndex(max(0, i))
        form.addRow("Idioma", self.lang_combo)
        _lang_note = QLabel("El idioma se aplica del todo al reiniciar la aplicación.")
        _lang_note.setObjectName("Hint")
        _lang_note.setWordWrap(True)
        form.addRow("", _lang_note)

        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 64)
        self.workers_spin.setValue(self._config.workers)
        form.addRow("Workers (hashing en paralelo)", self.workers_spin)

        self.gpu_check = QCheckBox("Usar GPU si está disponible (fase futura)")
        self.gpu_check.setChecked(self._config.use_gpu)
        self.gpu_check.setEnabled(False)
        form.addRow(self.gpu_check)

        self.deletion_combo = QComboBox()
        self.deletion_combo.addItem("Papelera del sistema (recuperable)", "trash")
        self.deletion_combo.addItem("Eliminación permanente", "permanent")
        idx = self.deletion_combo.findData(self._config.deletion_mode)
        self.deletion_combo.setCurrentIndex(max(0, idx))
        form.addRow("Modo de eliminación por defecto", self.deletion_combo)

        self.confirm_check = QCheckBox("Pedir siempre confirmación antes de eliminar")
        self.confirm_check.setChecked(self._config.confirm_deletions)
        self.confirm_check.setEnabled(False)  # confirmation is always required
        form.addRow(self.confirm_check)
        return w

    def _scan_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.symlink_check = QCheckBox("Seguir enlaces simbólicos (arriesgado: puede crear ciclos)")
        self.symlink_check.setChecked(self._config.follow_symlinks)
        form.addRow(self.symlink_check)

        self.minsize_spin = QDoubleSpinBox()
        self.minsize_spin.setRange(0.0, 10240.0)
        self.minsize_spin.setDecimals(2)
        self.minsize_spin.setSuffix(" MB")
        self.minsize_spin.setValue(self._config.min_size_mb)
        form.addRow("Ignorar archivos menores de", self.minsize_spin)

        self.ignored_ext_edit = QLineEdit(", ".join(self._config.ignored_extensions))
        self.ignored_ext_edit.setPlaceholderText("p. ej. .thm, .aae")
        form.addRow("Extensiones a ignorar", self.ignored_ext_edit)

        self.images_check = QCheckBox("Analizar imágenes")
        self.images_check.setChecked(self._config.analyze_images)
        form.addRow(self.images_check)

        self.pixel_check = QCheckBox(
            "Detectar imágenes pixel-por-pixel idénticas (decodifica candidatos)"
        )
        self.pixel_check.setChecked(self._config.detect_pixel_identical)
        form.addRow(self.pixel_check)

        self.videos_check = QCheckBox(
            "Analizar vídeos (hash de archivo + comparación de fotogramas, necesita FFmpeg)"
        )
        self.videos_check.setChecked(self._config.analyze_videos)
        form.addRow(self.videos_check)

        self.ffmpeg_edit = QLineEdit(self._config.ffmpeg_path)
        self.ffmpeg_edit.setPlaceholderText("vacío = detección automática (PATH / imageio-ffmpeg)")
        form.addRow("Ruta de FFmpeg (archivo o carpeta)", self.ffmpeg_edit)

        self.ffmpeg_status = QLabel("")
        self.ffmpeg_status.setWordWrap(True)
        self.ffmpeg_status.setObjectName("Hint")
        check_btn = QPushButton("Comprobar FFmpeg")
        check_btn.clicked.connect(self._check_ffmpeg)
        form.addRow(check_btn, self.ffmpeg_status)
        self._check_ffmpeg()

        self.similar_check = QCheckBox(
            "Analizar imágenes similares (motor combinado pHash+dHash+aHash+color+composición)"
        )
        self.similar_check.setChecked(self._config.analyze_similar_images)
        form.addRow(self.similar_check)

        self.sensitivity_combo = QComboBox()
        for text, key in [
            ("Baja — solo duplicados muy claros", "low"),
            ("Media — duplicados + imágenes muy similares", "medium"),
            ("Alta — también variantes, recortes y modificaciones", "high"),
            ("Personalizada", "custom"),
        ]:
            self.sensitivity_combo.addItem(text, key)
        i = self.sensitivity_combo.findData(self._config.sensitivity)
        self.sensitivity_combo.setCurrentIndex(max(0, i))
        self.sensitivity_combo.currentIndexChanged.connect(self._sync_custom)
        form.addRow("Sensibilidad de detección", self.sensitivity_combo)

        bands = self._config.similarity_bands or {}
        self.band_vi = QSpinBox()
        self.band_vi.setRange(50, 100)
        self.band_vi.setSuffix(" %")
        self.band_vi.setValue(int(bands.get("visually_identical", 98)))
        self.band_vs = QSpinBox()
        self.band_vs.setRange(50, 100)
        self.band_vs.setSuffix(" %")
        self.band_vs.setValue(int(bands.get("very_similar", 90)))
        self.band_si = QSpinBox()
        self.band_si.setRange(50, 100)
        self.band_si.setSuffix(" %")
        self.band_si.setValue(int(bands.get("similar", self._config.similarity_threshold)))
        form.addRow("  ⤷ Visualmente idéntico ≥", self.band_vi)
        form.addRow("  ⤷ Muy similar ≥", self.band_vs)
        form.addRow("  ⤷ Similar ≥ (no agrupar por debajo)", self.band_si)

        wts = self._config.similarity_weights or {}
        self.weight_spins: dict = {}
        for key, label in [
            ("phash", "pHash (estructura)"),
            ("dhash", "dHash (bordes)"),
            ("ahash", "aHash (tono)"),
            ("color", "histograma de color"),
            ("bhash", "composición (bloques)"),
        ]:
            sp = QSpinBox()
            sp.setRange(0, 100)
            sp.setSuffix(" %")
            sp.setValue(round(wts.get(key, 0.2) * 100))
            self.weight_spins[key] = sp
            form.addRow(f"  ⤷ peso {label}", sp)

        self.crop_check = QCheckBox("Detectar recortes (una imagen contenida en otra)")
        self.crop_check.setChecked(self._config.detect_crops)
        form.addRow(self.crop_check)

        self.ai_check = QCheckBox(
            "Detección avanzada mediante IA (embeddings visuales — opcional, requiere más recursos)"
        )
        self.ai_check.setChecked(self._config.use_ai_embeddings)
        self.ai_hint = QLabel("")
        self.ai_hint.setObjectName("Hint")
        self.ai_hint.setWordWrap(True)
        form.addRow(self.ai_check)
        form.addRow("", self.ai_hint)
        self._refresh_ai_hint()
        self.ai_check.toggled.connect(self._refresh_ai_hint)

        self._sync_custom()

        self.cache_check = QCheckBox("Usar caché en disco (SQLite) para acelerar reanálisis")
        self.cache_check.setChecked(self._config.use_cache)
        form.addRow(self.cache_check)

        self.cache_path_edit = QLineEdit(self._config.cache_db_path)
        self.cache_path_edit.setPlaceholderText(self._config.effective_db_path())
        form.addRow("Ruta de la caché", self.cache_path_edit)
        return w

    def _sync_custom(self) -> None:
        custom = self.sensitivity_combo.currentData() == "custom"
        for w in (
            self.band_vi,
            self.band_vs,
            self.band_si,
            self.crop_check,
            *self.weight_spins.values(),
        ):
            w.setEnabled(custom)

    def _refresh_ai_hint(self) -> None:
        if not self.ai_check.isChecked():
            self.ai_hint.setText("Desactivada: se usan solo los métodos tradicionales (rápidos).")
            return
        try:
            from app.core.embeddings import backend_status

            self.ai_hint.setText(backend_status())
        except Exception:
            self.ai_hint.setText(
                "Sin backend de IA instalado. Instala el extra:  pip install "
                '"pixmatch[ai]"  (open-clip-torch / onnxruntime).'
            )

    def _check_ffmpeg(self) -> None:
        from app.core.ffmpeg import detect, install_hint

        tools = detect(self.ffmpeg_edit.text(), self._config.ffprobe_path)
        if tools.available:
            probe = "con ffprobe" if tools.has_ffprobe else "sin ffprobe (se usa ffmpeg -i)"
            self.ffmpeg_status.setText(
                f"✅ FFmpeg detectado ({tools.source}, {probe}).\n{tools.ffmpeg}"
            )
        else:
            self.ffmpeg_status.setText("❌ FFmpeg no encontrado.\n" + install_hint())

    def _exclude_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(
            QLabel("Marca las carpetas que quieres excluir. Nada se excluye automáticamente.")
        )
        self.exclude_list = QListWidget()
        selected = set(self._config.excluded_dir_names)
        for name in sorted(set(COMMON_EXCLUDES) | selected):
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if name in selected else Qt.Unchecked)
            self.exclude_list.addItem(item)
        layout.addWidget(self.exclude_list, 1)

        layout.addWidget(QLabel("Añadir un nombre de carpeta:"))
        self.new_exclude_edit = QLineEdit()
        add_btn = QPushButton("Añadir a la lista")
        add_btn.clicked.connect(self._add_exclude)
        layout.addWidget(self.new_exclude_edit)
        layout.addWidget(add_btn)
        return w

    def _add_exclude(self) -> None:
        name = self.new_exclude_edit.text().strip()
        if not name:
            return
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        self.exclude_list.addItem(item)
        self.new_exclude_edit.clear()

    def _logs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel(f"Archivo de log: {LOG_FILE}"))
        view = QPlainTextEdit()
        view.setReadOnly(True)
        try:
            text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
            view.setPlainText(text[-40000:])
        except OSError:
            view.setPlainText("(sin log todavía)")
        layout.addWidget(view, 1)
        return w

    # ------------------------------------------------------------------
    def _save(self) -> None:
        c = self._config
        c.theme = self.theme_combo.currentText()
        c.language = self.lang_combo.currentData() or "es"
        c.workers = self.workers_spin.value()
        c.follow_symlinks = self.symlink_check.isChecked()
        c.min_size_mb = self.minsize_spin.value()
        c.ignored_extensions = [
            e.strip() for e in self.ignored_ext_edit.text().split(",") if e.strip()
        ]
        c.analyze_images = self.images_check.isChecked()
        c.deletion_mode = self.deletion_combo.currentData()
        c.detect_pixel_identical = self.pixel_check.isChecked()
        c.analyze_videos = self.videos_check.isChecked()
        c.ffmpeg_path = self.ffmpeg_edit.text().strip()
        c.analyze_similar_images = self.similar_check.isChecked()
        c.sensitivity = self.sensitivity_combo.currentData()
        c.similarity_bands = {
            "visually_identical": float(self.band_vi.value()),
            "very_similar": float(self.band_vs.value()),
            "similar": float(self.band_si.value()),
        }
        c.similarity_threshold = self.band_si.value()
        total_w = sum(sp.value() for sp in self.weight_spins.values()) or 1
        c.similarity_weights = {k: sp.value() / total_w for k, sp in self.weight_spins.items()}
        c.detect_crops = self.crop_check.isChecked()
        c.use_ai_embeddings = self.ai_check.isChecked()
        c.use_cache = self.cache_check.isChecked()
        c.cache_db_path = self.cache_path_edit.text().strip()
        excludes = []
        for i in range(self.exclude_list.count()):
            item = self.exclude_list.item(i)
            if item.checkState() == Qt.Checked:
                excludes.append(item.text())
        c.excluded_dir_names = excludes
        c.save()
        self.accept()
