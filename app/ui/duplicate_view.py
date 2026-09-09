"""The duplicate browser: filter / sort / search on the left, one group shown
in detail on the right, with side-by-side comparison and per-file decisions."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.duplicate_groups import AnalysisResult, Decision, DuplicateGroup
from app.core.recommendation import recommend_keep
from app.core.scanner import FileKind
from app.core.similarity import MatchCategory
from app.i18n import tr
from app.ui.advanced_compare import AdvancedCompareDialog
from app.ui.widgets.file_card import FileCard
from app.utils.file_utils import human_size

_FILTERS = [
    "Todas",
    "Imágenes",
    "Videos",
    "Archivo idéntico",
    "Pixel idéntico",
    "Redimensionados",
    "Recortes",
    "Similares",
    "Sin revisar",
    "Revisados",
    "Marcados para eliminar",
    "Resueltos",
]
_SORTS = [
    "Ahorro potencial",
    "Nº de duplicados",
    "Tamaño",
    "Similitud",
    "Nombre",
    "Ruta",
]

_SIMILAR_CATEGORIES = (
    MatchCategory.VISUALLY_IDENTICAL,
    MatchCategory.SAME_CONTENT_LIKELY,
    MatchCategory.RESIZED_DUPLICATE,
    MatchCategory.CROPPED_SIMILAR,
    MatchCategory.VERY_SIMILAR,
    MatchCategory.SIMILAR,
)


class DuplicateView(QWidget):
    decisions_changed = Signal()
    deletion_requested = Signal()  # delete everything marked
    single_deletion_requested = Signal(object)  # FileRecord - delete just this one

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._result: AnalysisResult | None = None
        self._visible: list[DuplicateGroup] = []
        self._index = 0

        # ---- left column: controls + group list ----
        # combos carry the canonical (Spanish) key as itemData; display text is
        # translated. All logic compares currentData(), never the label.
        self.filter_combo = QComboBox()
        for key in _FILTERS:
            self.filter_combo.addItem(tr(key), key)
        self.sort_combo = QComboBox()
        for key in _SORTS:
            self.sort_combo.addItem(tr(key), key)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("Buscar por nombre, extensión o ruta…"))
        self.min_count = QSpinBox()
        self.min_count.setRange(2, 999)
        self.min_count.setValue(2)
        self.min_count.setPrefix("≥ ")
        self.min_count.setSuffix(" archivos")

        for w in (self.filter_combo, self.sort_combo, self.min_count):
            w.currentIndexChanged.connect(self._rebuild) if isinstance(
                w, QComboBox
            ) else w.valueChanged.connect(self._rebuild)
        self.search_edit.textChanged.connect(self._rebuild)

        self.group_list = QListWidget()
        self.group_list.currentRowChanged.connect(self._on_list_row)

        left = QVBoxLayout()
        left.addWidget(QLabel(tr("Filtro")))
        left.addWidget(self.filter_combo)
        left.addWidget(QLabel(tr("Ordenar por")))
        left.addWidget(self.sort_combo)
        left.addWidget(self.search_edit)
        left.addWidget(self.min_count)
        left.addWidget(self.group_list, 1)
        left_widget = QWidget()
        left_widget.setLayout(left)
        left_widget.setFixedWidth(300)

        # ---- right column: current group ----
        self.header = QLabel()
        self.header.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.badge = QLabel()
        self.badge.setObjectName("Badge")
        self.reco = QLabel()
        self.reco.setObjectName("Hint")
        self.reco.setWordWrap(True)

        self.cards_container = QWidget()
        self.cards_layout = QHBoxLayout(self.cards_container)
        self.cards_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setWidget(self.cards_container)

        self.keep_sel_btn = QPushButton(tr("Mantener seleccionados"))
        self.del_unsel_btn = QPushButton(tr("Eliminar no seleccionados"))
        self.del_unsel_btn.setObjectName("danger")
        self.keep_all_btn = QPushButton(tr("Mantener todos"))
        self.ignore_btn = QPushButton(tr("Ignorar grupo"))
        self.clear_btn = QPushButton(tr("Quitar decisiones"))
        self.advanced_btn = QPushButton(tr("🔍 Comparar en detalle"))
        self.keep_sel_btn.setToolTip(
            "Conservar los archivos marcados y marcar el resto para eliminar"
        )
        self.del_unsel_btn.setToolTip("Marcar para eliminar todo lo que NO esté seleccionado")
        self.ignore_btn.setToolTip("Sacar este grupo de la revisión sin borrar nada")
        self.keep_sel_btn.clicked.connect(self.keep_selected)
        self.del_unsel_btn.clicked.connect(self.delete_unselected)
        self.keep_all_btn.clicked.connect(self.keep_all)
        self.ignore_btn.clicked.connect(self.ignore_group)
        self.clear_btn.clicked.connect(self.clear_group_decisions)
        self.advanced_btn.clicked.connect(self.open_advanced_compare)

        self._group_action_buttons = [
            self.keep_sel_btn,
            self.del_unsel_btn,
            self.keep_all_btn,
            self.ignore_btn,
            self.clear_btn,
        ]
        actions = QHBoxLayout()
        for b in (*self._group_action_buttons, self.advanced_btn):
            actions.addWidget(b)
        actions.addStretch(1)

        self.prev_btn = QPushButton(tr("◀ Anterior"))
        self.next_btn = QPushButton(tr("Siguiente ▶"))
        self.counter = QLabel("—")
        self.prev_btn.clicked.connect(self.previous_group)
        self.next_btn.clicked.connect(self.next_group)
        nav = QHBoxLayout()
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.counter, 1, Qt.AlignCenter)
        nav.addWidget(self.next_btn)

        self.savings = QLabel("")
        self.savings.setStyleSheet("font-weight: 600;")

        # global "delete what's marked" footer
        self.marked_summary = QLabel(tr("Nada marcado para eliminar."))
        self.marked_summary.setObjectName("Hint")
        self.delete_marked_btn = QPushButton(tr("🗑  Eliminar archivos marcados…"))
        self.delete_marked_btn.setObjectName("danger")
        self.delete_marked_btn.setEnabled(False)
        self.delete_marked_btn.clicked.connect(self.deletion_requested.emit)
        footer = QHBoxLayout()
        footer.addWidget(self.marked_summary, 1)
        footer.addWidget(self.delete_marked_btn)

        right = QVBoxLayout()
        head_row = QHBoxLayout()
        head_row.addWidget(self.header)
        head_row.addWidget(self.badge, 0, Qt.AlignVCenter)
        head_row.addStretch(1)
        right.addLayout(head_row)
        right.addWidget(self.reco)
        right.addWidget(self.cards_scroll, 1)
        right.addLayout(actions)
        right.addWidget(self.savings)
        right.addLayout(footer)
        right.addLayout(nav)
        right_widget = QWidget()
        right_widget.setLayout(right)

        root = QHBoxLayout(self)
        root.addWidget(left_widget)
        root.addWidget(right_widget, 1)

        self._empty()

    # ==================================================================
    def load(self, result: AnalysisResult) -> None:
        self._result = result
        self.filter_combo.setCurrentIndex(0)
        self.search_edit.clear()
        self.min_count.setValue(2)
        self._rebuild()
        self.refresh_marked_summary()

    def refresh_marked_summary(self) -> None:
        if self._result is None:
            self.delete_marked_btn.setEnabled(False)
            self.marked_summary.setText(tr("Nada marcado para eliminar."))
            return
        marked = [
            r
            for g in self._result.groups
            if not g.ignored
            for r in g.marked_for_deletion
            if not r.deleted
        ]
        freed = sum(r.size for r in marked)
        self.delete_marked_btn.setEnabled(bool(marked))
        if marked:
            self.marked_summary.setText(
                f"Marcados para eliminar: {len(marked)} archivo(s)  ·  "
                f"liberaría {human_size(freed)}"
            )
        else:
            self.marked_summary.setText(tr("Nada marcado para eliminar."))

    # -- filtering / sorting -------------------------------------------
    def _rebuild(self) -> None:
        if self._result is None:
            self._empty()
            return

        current_id = self._current_group().group_id if self._visible else None

        groups = list(self._result.groups)
        f = self.filter_combo.currentData() or "Todas"
        query = self.search_edit.text().strip().lower()
        minc = self.min_count.value()

        def matches_filter(g: DuplicateGroup) -> bool:
            if f == "Resueltos":
                return g.is_resolved
            # every other filter hides groups that are already resolved
            if g.is_resolved:
                return False
            if g.count < minc:
                return False
            if f == "Imágenes":
                return all(r.kind is FileKind.IMAGE for r in g.records)
            if f == "Videos":
                return all(r.kind is FileKind.VIDEO for r in g.records)
            if f == "Archivo idéntico":
                return g.category is MatchCategory.FILE_IDENTICAL
            if f == "Pixel idéntico":
                return g.category is MatchCategory.PIXEL_IDENTICAL
            if f == "Redimensionados":
                return g.category is MatchCategory.RESIZED_DUPLICATE
            if f == "Recortes":
                return g.category is MatchCategory.CROPPED_SIMILAR
            if f == "Similares":
                return g.category in _SIMILAR_CATEGORIES
            if f == "Sin revisar":
                return not g.is_reviewed
            if f == "Revisados":
                return g.is_reviewed
            if f == "Marcados para eliminar":
                return bool([r for r in g.marked_for_deletion if not r.deleted])
            return True

        def matches_query(g: DuplicateGroup) -> bool:
            if not query:
                return True
            return any(query in r.name.lower() or query in r.path.lower() for r in g.records)

        groups = [g for g in groups if matches_filter(g) and matches_query(g)]

        s = self.sort_combo.currentData() or "Ahorro potencial"
        keyfns = {
            "Ahorro potencial": lambda g: g.potential_reclaimable,
            "Nº de duplicados": lambda g: g.count,
            "Tamaño": lambda g: g.unit_size,
            "Similitud": lambda g: g.similarity_percent or (100.0 if g.is_exact else 0.0),
            "Nombre": lambda g: g.records[0].name.lower(),
            "Ruta": lambda g: g.records[0].path.lower(),
        }
        reverse = s in ("Ahorro potencial", "Nº de duplicados", "Tamaño", "Similitud")
        groups.sort(key=keyfns.get(s, keyfns["Ahorro potencial"]), reverse=reverse)

        self._visible = groups
        self.group_list.blockSignals(True)
        self.group_list.clear()
        for g in groups:
            tag = g.category.style.emoji
            item = QListWidgetItem(
                f"#{g.group_id} {tag}  ·  {g.count} arch.  ·  {human_size(g.potential_reclaimable)}"
            )
            if g.ignored:
                item.setText(item.text() + "  (ignorado)")
            self.group_list.addItem(item)
        self.group_list.blockSignals(False)

        if not groups:
            self._empty()
            return

        new_index = 0
        if current_id is not None:
            for i, g in enumerate(groups):
                if g.group_id == current_id:
                    new_index = i
                    break
        self._show(new_index)

    # -- navigation ---------------------------------------------------
    def _current_group(self) -> DuplicateGroup:
        return self._visible[self._index]

    def _on_list_row(self, row: int) -> None:
        if 0 <= row < len(self._visible) and row != self._index:
            self._show(row)

    def _show(self, index: int) -> None:
        if not self._visible:
            self._empty()
            return
        self._index = max(0, min(index, len(self._visible) - 1))
        group = self._current_group()

        self.group_list.blockSignals(True)
        self.group_list.setCurrentRow(self._index)
        self.group_list.blockSignals(False)

        style = group.category.style
        noun = "archivos" if group.is_exact else "archivos relacionados"
        sim = f"  ·  confianza {group.confidence}%"
        if group.similarity_percent is not None and not group.is_exact:
            sim = f"  ·  similitud ≈ {group.similarity_percent:.1f}%  ·  confianza {group.confidence}%"
        deleted_note = ""
        if group.deleted_records:
            deleted_note = f"  ·  {len(group.deleted_records)} ya eliminado(s)"
        self.header.setText(f"Grupo #{group.group_id} — {group.count} {noun}{sim}{deleted_note}")
        self.badge.setText(f"{style.emoji} {style.short_es}")
        self.badge.setStyleSheet(f"#Badge {{ background: {style.color}; }}")

        parts = [f"<b>{style.short_es}</b> — {style.description_es}"]
        if group.match_reasons:
            checks = "".join(f"<br>&nbsp;&nbsp;✓ {r}" for r in group.match_reasons)
            parts.append(f"<b>Se agruparon porque:</b>{checks}")
        if not group.is_exact:
            parts.append(
                "⚠️ Es <b>similar</b>, no idéntico. Usa «Comparar en detalle» "
                "para ver las diferencias antes de decidir."
            )
        # per-member relation ("small → 98.7%")
        members_by_sim = sorted(group.records, key=lambda r: -(r.similarity_percent or 0))
        rel = " · ".join(
            f"{r.name} {r.similarity_percent:.1f}%"
            for r in members_by_sim
            if r.similarity_percent is not None
        )
        if rel and not group.is_exact:
            parts.append(f"<b>Relación:</b> {rel}")
        rec = recommend_keep(group)
        if rec:
            parts.append(
                f"💡 Recomendación (no vinculante): conservar <b>{rec.record.name}</b> "
                f"· confianza {rec.confidence}% · {', '.join(rec.reasons)}. "
                f"Tú decides; nada se selecciona automáticamente para borrar."
            )
        self.reco.setText("<br>".join(parts))
        is_video_group = all(r.kind is FileKind.VIDEO for r in group.records)
        self.advanced_btn.setText(
            tr("🎬 Comparar vídeos") if is_video_group else tr("🔍 Comparar en detalle")
        )
        self.advanced_btn.setVisible(group.count >= 2 and (not group.is_exact or is_video_group))

        # rebuild cards
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._cards: list[FileCard] = []
        for r in group.records:
            card = FileCard(r, recommended=bool(rec and rec.record is r))
            card.decision_changed.connect(self._on_card_decision)
            card.zoom_requested.connect(self._open_zoom)
            card.delete_requested.connect(self.single_deletion_requested)
            self.cards_layout.addWidget(card)
            self._cards.append(card)
        self.cards_layout.addStretch(1)

        self.counter.setText(f"Grupo {self._index + 1} de {len(self._visible)}")
        self.prev_btn.setEnabled(self._index > 0)
        self.next_btn.setEnabled(self._index < len(self._visible) - 1)
        for c in self._cards:
            c.keep_checkbox.toggled.connect(self._refresh_group_buttons)
        self._refresh_group_buttons()
        self._update_savings()

    def _refresh_group_buttons(self) -> None:
        cards = getattr(self, "_cards", [])
        active = [c for c in cards if not c.record.deleted]
        has_group = bool(self._visible) and len(active) >= 1
        checked = [c for c in active if c.keep_checkbox.isChecked()]
        any_decision = any(c.record.decision.value != "undecided" for c in active) or (
            self._visible and self._current_group().ignored
        )

        # "keep selected" / "delete unselected" only make sense with a partial
        # selection (all-checked is just "keep all", none-checked is a no-op)
        meaningful = has_group and 0 < len(checked) < len(active)
        self.keep_all_btn.setEnabled(has_group and len(active) >= 2)
        self.ignore_btn.setEnabled(bool(self._visible))
        self.clear_btn.setEnabled(bool(any_decision))
        self.keep_sel_btn.setEnabled(meaningful)
        self.del_unsel_btn.setEnabled(meaningful)

    def _empty(self) -> None:
        self._visible = []
        self._index = 0
        for attr in ("_cards",):
            if hasattr(self, attr):
                for c in getattr(self, attr):
                    c.deleteLater()
                setattr(self, attr, [])
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.header.setText("Sin grupos que mostrar con el filtro actual")
        self.badge.setText("")
        self.badge.setStyleSheet("")
        self.reco.setText("")
        self.counter.setText("—")
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self.savings.setText("")
        self.advanced_btn.setVisible(False)
        for b in self._group_action_buttons:
            b.setEnabled(False)

    # -- decisions --------------------------------------------------
    def _on_card_decision(self) -> None:
        self._update_savings()
        self._refresh_group_buttons()
        self.refresh_marked_summary()
        self.decisions_changed.emit()

    def _update_savings(self) -> None:
        if not self._visible:
            return
        g = self._current_group()
        n = len(g.marked_for_deletion)
        self.savings.setText(
            f"En este grupo: {n} archivo(s) marcado(s) para eliminar · "
            f"liberaría {human_size(g.reclaimable_bytes)}"
        )
        if g.has_unsafe_decision:
            self.savings.setText(
                self.savings.text() + "   ⚠️ Todos están marcados para eliminar: quedaría 0 copias."
            )

    def keep_selected(self) -> None:
        any_selected = any(c.keep_checkbox.isChecked() for c in self._cards)
        if not any_selected:
            QMessageBox.information(
                self,
                "Nada seleccionado",
                "Marca al menos un archivo con «Mantener este archivo».",
            )
            return
        for c in self._cards:
            c.set_decision(Decision.KEEP if c.keep_checkbox.isChecked() else Decision.DELETE)
        self._update_savings()

    def delete_unselected(self) -> None:
        self.keep_selected()

    def keep_all(self) -> None:
        for c in self._cards:
            c.set_decision(Decision.KEEP)
        self._update_savings()

    def clear_group_decisions(self) -> None:
        for c in self._cards:
            c.set_decision(Decision.UNDECIDED)
        self._current_group().ignored = False
        self._update_savings()

    def ignore_group(self) -> None:
        g = self._current_group()
        g.ignored = True
        for r in g.records:
            r.reviewed = True
        self._rebuild()
        self.refresh_marked_summary()

    def after_deletion(self) -> None:
        """Called by the window once files were deleted. The deleted files stay
        visible (greyed, marked ELIMINADO) but resolved groups drop out of the
        default filters; they remain under the "Resueltos" filter and in
        exports."""
        self._rebuild()
        self.refresh_marked_summary()

    def _open_zoom(self, record) -> None:
        if not self._visible:
            return
        group = self._current_group()
        images = [r for r in group.records if r.kind is FileKind.IMAGE]
        if not images:
            return
        try:
            start = images.index(record)
        except ValueError:
            start = 0
        from app.ui.image_viewer import ImageViewerDialog

        dlg = ImageViewerDialog(images, start, self)
        dlg.decision_changed.connect(self._on_card_decision)
        dlg.exec()
        if dlg.pending_delete is not None:
            self.single_deletion_requested.emit(dlg.pending_delete)

    def open_advanced_compare(self) -> None:
        if not self._visible:
            return
        group = self._current_group()

        videos = [r for r in group.records if r.kind is FileKind.VIDEO]
        if len(videos) >= 2 and len(videos) == group.count:
            from app.ui.video_viewer import VideoCompareDialog

            VideoCompareDialog(videos, self).exec()
            return

        images = [r for r in group.records if r.kind is FileKind.IMAGE]
        if len(images) < 2:
            QMessageBox.information(
                self,
                "Comparación avanzada",
                "Se necesitan al menos dos elementos comparables en el grupo.",
            )
            return
        AdvancedCompareDialog(images, self).exec()

    # -- keyboard actions (called from MainWindow) ---------------------
    def act_keep(self) -> None:
        for c in self._cards:
            if c.underMouse():
                c.set_decision(Decision.KEEP)
                return
        self.keep_all()

    def act_delete(self) -> None:
        for c in self._cards:
            if c.underMouse():
                c.set_decision(Decision.DELETE)
                return

    def next_group(self) -> None:
        self._show(self._index + 1)

    def previous_group(self) -> None:
        self._show(self._index - 1)
