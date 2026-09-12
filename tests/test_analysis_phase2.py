"""Phase 2 pipeline: pixel-identical, perceptual similarity and the cache."""

from __future__ import annotations

import shutil

from PIL import Image, ImageDraw

from app.config import AppConfig
from app.core.analysis import run_analysis
from app.core.similarity import MatchCategory


def _cfg(tmp_path=None, **kw) -> AppConfig:
    c = AppConfig(analyze_images=True, analyze_videos=False, workers=2, use_cache=False)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _photo(path, size=(360, 270), seed=0):
    img = Image.new("RGB", size, (24, 28, 40))
    d = ImageDraw.Draw(img)
    for i in range(14):
        x = (i * 41 + seed * 17) % size[0]
        y = (i * 29 + seed * 11) % size[1]
        d.ellipse([x, y, x + 55, y + 55], fill=(190 - i * 9, 40 + i * 7, 100 + seed))
    d.rectangle([30, 30, 150, 150], fill=(230, 200, 25))
    img.save(path)
    return path


# -- pixel identical --------------------------------------------------
def test_png_and_bmp_same_pixels_is_pixel_identical(tmp_path):
    a = tmp_path / "shot.png"
    _photo(a)
    b = tmp_path / "shot_copy.bmp"
    Image.open(a).save(b)

    groups = run_analysis(str(tmp_path), _cfg()).groups
    assert len(groups) == 1
    assert groups[0].category is MatchCategory.PIXEL_IDENTICAL


def test_byte_identical_still_file_identical_in_phase2(tmp_path):
    a = _photo(tmp_path / "a.png")
    shutil.copy2(a, tmp_path / "a_backup.png")
    groups = run_analysis(str(tmp_path), _cfg()).groups
    assert len(groups) == 1
    assert groups[0].category is MatchCategory.FILE_IDENTICAL


# -- perceptual similarity ------------------------------------------
def test_resized_copy_is_similar_only_when_enabled(tmp_path):
    a = _photo(tmp_path / "original.png", size=(600, 450))
    Image.open(a).resize((300, 225)).save(tmp_path / "original_small.jpg", quality=75)

    # perceptual off -> different dimensions, different bytes, not grouped
    assert run_analysis(str(tmp_path), _cfg(analyze_similar_images=False)).groups == []

    # perceptual on -> grouped; a plain downscale is labelled RESIZED_DUPLICATE
    groups = run_analysis(str(tmp_path), _cfg(analyze_similar_images=True)).groups
    assert len(groups) == 1
    assert groups[0].category in (
        MatchCategory.RESIZED_DUPLICATE,
        MatchCategory.VISUALLY_IDENTICAL,
        MatchCategory.VERY_SIMILAR,
        MatchCategory.SIMILAR,
    )
    assert groups[0].similarity_percent is not None
    assert any(r.similarity_percent is not None for r in groups[0].records)
    assert groups[0].match_reasons  # "why they were grouped"


def test_unrelated_images_not_grouped_as_similar(tmp_path):
    _photo(tmp_path / "one.png", seed=2)
    _photo(tmp_path / "two.png", seed=250)
    groups = run_analysis(str(tmp_path), _cfg(analyze_similar_images=True)).groups
    assert groups == []


# -- cache -------------------------------------------------------
def test_incremental_run_reuses_cache(tmp_path):
    db = tmp_path / "c.sqlite3"
    a = _photo(tmp_path / "a.png")
    shutil.copy2(a, tmp_path / "a2.png")

    first = run_analysis(str(tmp_path), _cfg(), db_path=str(db))
    assert first.hashed_files >= 2
    assert first.cache_hits == 0

    second = run_analysis(str(tmp_path), _cfg(), db_path=str(db), incremental=True)
    assert second.cache_hits >= 2
    assert second.groups and second.groups[0].category is MatchCategory.FILE_IDENTICAL


def test_cache_invalidated_when_file_changes(tmp_path):
    db = tmp_path / "c.sqlite3"
    a = tmp_path / "a.png"
    _photo(a)
    shutil.copy2(a, tmp_path / "a2.png")
    run_analysis(str(tmp_path), _cfg(), db_path=str(db))

    # modify one file so size+mtime change
    import os
    import time

    time.sleep(0.01)
    _photo(a, seed=123)
    os.utime(a, None)

    result = run_analysis(str(tmp_path), _cfg(), db_path=str(db), incremental=True)
    # 'a.png' must be re-hashed (cache invalid); the two files now differ so no group
    assert result.groups == []
    assert result.cache_hits <= 1


def test_pause_blocks_the_pixel_phase(tmp_path):
    import threading
    import time

    from app.core.analysis import AnalysisCallbacks
    from app.utils.control import RunController

    # lots of same-size images so the decode-heavy phases take real time
    for i in range(150):
        _photo(tmp_path / f"p{i}.png", size=(600, 460), seed=i % 4)

    ctrl = RunController()
    in_decode_phase = threading.Event()

    class CB(AnalysisCallbacks):
        def on_step_progress(self, progress):
            if progress.label in ("Metadatos", "Comparación de píxeles") and progress.done > 0:
                in_decode_phase.set()

    box = {}

    def go():
        box["r"] = run_analysis(str(tmp_path), _cfg(), controller=ctrl, callbacks=CB())

    t = threading.Thread(target=go)
    t.start()
    assert in_decode_phase.wait(15)
    ctrl.pause()
    time.sleep(0.6)
    t.join(timeout=0.2)
    assert "r" not in box, "run should be paused mid-decode"
    ctrl.resume()
    t.join(timeout=25)
    assert "r" in box and box["r"].groups


def test_missing_dir_does_not_crash_with_cache(tmp_path):
    result = run_analysis(str(tmp_path / "nope"), _cfg(), db_path=str(tmp_path / "c.sqlite3"))
    assert result.groups == []
