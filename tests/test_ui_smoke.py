"""Smoke tests for the GUI layer under the offscreen platform.

These do not drive a real event loop - they just make sure the widgets can be
constructed and populated with a real AnalysisResult without raising.
"""

from __future__ import annotations

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.config import AppConfig
from app.core.analysis import run_analysis


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_constructs(qapp):
    from app.ui.main_window import MainWindow

    win = MainWindow(AppConfig())
    assert win.windowTitle().startswith("PixMatch")
    assert win.tabs.count() == 2  # Duplicados + Renombrar
    win.close()


def test_rename_view_preview_and_apply(qapp, tmp_path):
    from datetime import datetime

    from PIL import Image

    from app.database.history import OperationHistory
    from app.ui.rename_view import RenameView

    def jpeg(name, dt):
        p = tmp_path / name
        img = Image.new("RGB", (16, 12), (1, 2, 3))
        exif = img.getexif()
        exif.get_ifd(0x8769)[0x9003] = dt.strftime("%Y:%m:%d %H:%M:%S")
        img.save(p, exif=exif)

    jpeg("DSC1.jpg", datetime(2022, 12, 2, 14, 30, 5))
    jpeg("DSC2.jpg", datetime(2022, 12, 2, 14, 30, 5))  # same second -> suffix

    hist = OperationHistory(tmp_path / "h.sqlite3")
    view = RenameView(AppConfig(use_cache=False), hist)
    view.set_folder(str(tmp_path))

    # run the preview synchronously
    from app.core.renamer import build_rename_plan
    from app.core.scanner import FileKind

    class F:
        def __init__(self, p):
            import os

            self.path = str(p)
            self.kind = FileKind.IMAGE
            self.mtime = os.stat(p).st_mtime

    view._plans = build_rename_plan([F(tmp_path / "DSC1.jpg"), F(tmp_path / "DSC2.jpg")])
    view._repopulate_table()
    assert view.table.rowCount() == 2
    assert view.rename_btn.isEnabled()
    news = sorted(p.new_name for p in view._plans)
    assert news == ["20221202_143005.jpg", "20221202_143005_2.jpg"]

    hist.close()
    view.deleteLater()


def test_duplicate_view_loads_result(qapp, tmp_path, make_image):
    src = make_image(tmp_path / "a.jpg", size=(40, 30))
    shutil.copy2(src, tmp_path / "a_copy.jpg")
    result = run_analysis(str(tmp_path), AppConfig(workers=2, use_cache=False))
    assert result.groups

    from app.ui.duplicate_view import DuplicateView

    view = DuplicateView()
    view.load(result)
    assert view.group_list.count() == len(result.groups)

    # exercise a decision path
    view._show(0)

    # group-action buttons reflect what is actually possible
    assert not view.keep_sel_btn.isEnabled()  # nothing selected yet
    assert not view.clear_btn.isEnabled()  # no decisions yet
    assert view.keep_all_btn.isEnabled()
    view._cards[0].keep_checkbox.setChecked(True)
    assert view.keep_sel_btn.isEnabled()  # partial selection
    assert view.del_unsel_btn.isEnabled()

    view.keep_all()
    assert all(r.decision.value == "keep" for r in result.groups[0].records)
    view.deleteLater()


def test_similar_group_and_advanced_compare(qapp, tmp_path):
    from PIL import Image, ImageDraw

    from app.core.similarity import MatchCategory
    from app.ui.advanced_compare import AdvancedCompareDialog
    from app.ui.duplicate_view import DuplicateView

    big = tmp_path / "big.png"
    img = Image.new("RGB", (500, 380), (20, 30, 50))
    d = ImageDraw.Draw(img)
    for i in range(16):
        d.ellipse([i * 20, i * 12, i * 20 + 70, i * 12 + 70], fill=(180 - i * 6, 40 + i * 6, 120))
    img.save(big)
    Image.open(big).resize((250, 190)).save(tmp_path / "small.jpg", quality=72)

    result = run_analysis(
        str(tmp_path),
        AppConfig(workers=2, use_cache=False, analyze_similar_images=True),
    )
    assert result.similar_groups
    group = result.similar_groups[0]
    assert group.category in (
        MatchCategory.RESIZED_DUPLICATE,
        MatchCategory.VISUALLY_IDENTICAL,
        MatchCategory.VERY_SIMILAR,
        MatchCategory.SIMILAR,
    )

    view = DuplicateView()
    view.load(result)
    view.filter_combo.setCurrentText("Similares")
    assert view.group_list.count() >= 1
    assert not view.advanced_btn.isHidden()  # shown for a similar group

    dlg = AdvancedCompareDialog(group.records)
    dlg._refresh()
    dlg.deleteLater()
    view.deleteLater()


def test_file_card_has_zoom_and_delete(qapp, tmp_path, make_image):
    import shutil

    from app.core.analysis import run_analysis

    src = make_image(tmp_path / "a.jpg", size=(50, 40))
    shutil.copy2(src, tmp_path / "a_copy.jpg")
    result = run_analysis(str(tmp_path), AppConfig(workers=2, use_cache=False))

    from app.ui.duplicate_view import DuplicateView

    view = DuplicateView()
    view.load(result)
    view._show(0)
    card = view._cards[0]
    assert card.zoom_btn.text().startswith("🔍")
    assert card.delete_btn.text().startswith("🗑")

    got = []
    view.single_deletion_requested.connect(got.append)
    card.delete_btn.click()
    assert got and got[0] is card.record
    view.deleteLater()


def test_image_viewer_navigation_and_pending_delete(qapp, tmp_path, make_image):
    from app.core.duplicate_groups import Decision, FileRecord
    from app.core.scanner import FileKind
    from app.ui.image_viewer import ImageViewerDialog

    paths = [
        make_image(tmp_path / f"v{i}.png", size=(40, 30), color=(i * 40, 0, 0)) for i in range(3)
    ]
    recs = [
        FileRecord(path=str(p), size=p.stat().st_size, mtime=1.0, kind=FileKind.IMAGE)
        for p in paths
    ]

    dlg = ImageViewerDialog(recs, 0)
    assert dlg._counter.text() == "1 / 3"
    dlg._go(1)
    assert dlg._current() is recs[1]
    dlg._set_decision(Decision.KEEP)
    assert recs[1].decision is Decision.KEEP
    dlg._request_delete()
    assert dlg.pending_delete is recs[1]
    dlg.deleteLater()


def test_deletion_flow_marks_group_resolved(qapp, tmp_path, make_image):
    import shutil

    from app.core.deletion_manager import DeletionMode, build_preview
    from app.core.duplicate_groups import Decision
    from app.database.history import OperationHistory
    from app.ui.deletion_dialog import DeletionConfirmDialog
    from app.ui.duplicate_view import DuplicateView
    from app.workers.deletion_worker import DeletionRunner

    src = make_image(tmp_path / "p.jpg", size=(40, 30))
    shutil.copy2(src, tmp_path / "p2.jpg")
    shutil.copy2(src, tmp_path / "p3.jpg")
    result = run_analysis(str(tmp_path), AppConfig(workers=2, use_cache=False))
    assert result.groups and result.groups[0].count == 3

    view = DuplicateView()
    view.load(result)
    assert not view.delete_marked_btn.isEnabled()

    for rec in result.groups[0].records[1:]:
        rec.decision = Decision.DELETE
    view.refresh_marked_summary()
    assert view.delete_marked_btn.isEnabled()

    preview = build_preview(result.groups)
    assert preview.count == 2
    dlg = DeletionConfirmDialog(preview, DeletionMode.TRASH)
    dlg._accept()
    assert dlg.chosen_mode is DeletionMode.TRASH
    dlg.deleteLater()

    hist = OperationHistory(tmp_path / "h.sqlite3")
    runner = DeletionRunner(preview.items, DeletionMode.PERMANENT, hist)
    done = {}
    runner.finished.connect(lambda r: done.setdefault("r", r))
    runner.start()
    for _ in range(200):
        qapp.processEvents()
        if done:
            break
        import time

        time.sleep(0.02)
    runner.wait()
    assert done["r"].freed_bytes > 0

    view.after_deletion()
    assert result.groups[0].is_resolved
    assert view.group_list.count() == 0  # resolved group hidden by default
    view.filter_combo.setCurrentText("Resueltos")
    assert view.group_list.count() == 1
    hist.close()
    view.deleteLater()
