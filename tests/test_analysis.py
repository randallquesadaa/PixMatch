"""End-to-end Phase 1 pipeline tests (scan -> hash -> group)."""

from __future__ import annotations

import os
import shutil

import pytest
from PIL import Image

from app.config import AppConfig
from app.core.analysis import run_analysis
from app.core.metadata import read_image_info
from app.utils.control import RunController


def _cfg(**kw) -> AppConfig:
    c = AppConfig(analyze_images=True, analyze_videos=False, workers=2, use_cache=False)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_detects_byte_identical_copies(tmp_path, make_image):
    src = make_image(tmp_path / "album" / "IMG_1234.jpg", size=(120, 90))
    dst = tmp_path / "backup" / "Copy of IMG_1234.jpg"
    dst.parent.mkdir()
    shutil.copy2(src, dst)
    # an unrelated image
    make_image(tmp_path / "album" / "other.jpg", size=(50, 50), color=(0, 200, 0))

    result = run_analysis(str(tmp_path), _cfg())
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.count == 2
    assert {r.name for r in group.records} == {"IMG_1234.jpg", "Copy of IMG_1234.jpg"}
    assert group.records[0].sha256 == group.records[1].sha256
    assert result.reclaimable_bytes == group.unit_size


def test_same_pixels_different_bytes_are_NOT_an_exact_group(tmp_path):
    """A PNG and a JPEG of the same scene must not be reported as FILE IDENTICAL
    in phase 1 (that is PIXEL IDENTICAL / VERY SIMILAR, handled in phase 2)."""
    a = tmp_path / "a.png"
    b = tmp_path / "b.jpg"
    img = Image.new("RGB", (80, 60), (123, 222, 64))
    img.save(a, format="PNG")
    img.save(b, format="JPEG", quality=95)

    result = run_analysis(str(tmp_path), _cfg())
    assert result.groups == []


def test_same_image_different_metadata_is_pixel_identical(tmp_path):
    """Same pixels, different bytes (one PNG carries a text chunk) -> the group
    must be PIXEL_IDENTICAL, never FILE_IDENTICAL."""
    from app.core.similarity import MatchCategory

    a = tmp_path / "with_comment.png"
    b = tmp_path / "no_comment.png"
    img = Image.new("RGB", (40, 40), (10, 10, 10))
    from PIL import PngImagePlugin

    meta = PngImagePlugin.PngInfo()
    meta.add_text("Comment", "hello")
    img.save(a, format="PNG", pnginfo=meta)
    img.save(b, format="PNG")

    groups = run_analysis(str(tmp_path), _cfg()).groups
    assert len(groups) == 1
    assert groups[0].category is MatchCategory.PIXEL_IDENTICAL
    # genuinely different files (different byte size), same decoded pixels
    assert groups[0].records[0].size != groups[0].records[1].size
    assert len({r.pixel_digest for r in groups[0].records}) == 1


def test_pixel_detection_can_be_disabled(tmp_path):
    a = tmp_path / "x.png"
    b = tmp_path / "y.png"
    img = Image.new("RGB", (40, 40), (10, 10, 10))
    from PIL import PngImagePlugin

    meta = PngImagePlugin.PngInfo()
    meta.add_text("Comment", "hello")
    img.save(a, format="PNG", pnginfo=meta)
    img.save(b, format="PNG")

    assert run_analysis(str(tmp_path), _cfg(detect_pixel_identical=False)).groups == []


def test_size_collision_without_content_match_is_not_grouped(tmp_path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"X" * 4096)
    b.write_bytes(b"Y" * 4096)  # same size, different content
    assert run_analysis(str(tmp_path), _cfg()).groups == []


def test_unicode_tree(tmp_path, make_image):
    base = tmp_path / "Fotos ☀" / "Cámara"
    src = make_image(base / "señal.png", size=(30, 30))
    shutil.copy2(src, base / "señal (copia).png")
    result = run_analysis(str(tmp_path), _cfg())
    assert len(result.groups) == 1
    assert result.groups[0].count == 2


def test_incremental_flag_passthrough(tmp_path, make_image):
    make_image(tmp_path / "x.jpg")
    r = run_analysis(str(tmp_path), _cfg(), incremental=True)
    assert r.incremental is True


def test_pause_then_resume_completes(tmp_path, make_image):
    import threading

    src = make_image(tmp_path / "p.jpg", size=(20, 20))
    shutil.copy2(src, tmp_path / "p_copy.jpg")

    ctrl = RunController()
    ctrl.pause()  # pause before the worker even starts

    box: dict = {}

    def go():
        box["result"] = run_analysis(str(tmp_path), _cfg(), controller=ctrl)

    t = threading.Thread(target=go)
    t.start()
    t.join(timeout=0.6)
    assert "result" not in box, "analysis must not progress while paused"

    ctrl.resume()
    t.join(timeout=5)
    assert box["result"].groups and box["result"].cancelled is False


def test_cancel_midway_returns_partial_and_flagged(tmp_path, make_image):
    for i in range(6):
        src = make_image(tmp_path / f"a{i}.jpg", size=(20, 20))
        shutil.copy2(src, tmp_path / f"a{i}_copy.jpg")
    ctrl = RunController()
    ctrl.cancel()
    result = run_analysis(str(tmp_path), _cfg(), controller=ctrl)
    assert result.cancelled is True


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX perms, non-root")
def test_unreadable_file_does_not_abort(tmp_path, make_image):
    src = make_image(tmp_path / "ok.jpg", size=(24, 24))
    shutil.copy2(src, tmp_path / "ok_copy.jpg")
    bad = tmp_path / "locked.jpg"
    shutil.copy2(src, bad)
    another = tmp_path / "locked2.jpg"
    shutil.copy2(src, another)
    os.chmod(bad, 0o000)
    try:
        result = run_analysis(str(tmp_path), _cfg())
        # ok.jpg + ok_copy.jpg + locked2.jpg all share content -> a group forms,
        # the unreadable file is skipped with an error record, no crash.
        assert result.groups
        assert result.groups[0].count >= 2
    finally:
        os.chmod(bad, 0o644)


def test_exif_orientation_metadata_is_read(tmp_path):
    p = tmp_path / "rot.jpg"
    img = Image.new("RGB", (100, 40), (5, 5, 5))
    exif = img.getexif()
    exif[0x0112] = 6  # rotate 90 CW
    img.save(p, format="JPEG", exif=exif)

    info = read_image_info(p)
    assert info.orientation == 6
    assert info.oriented_size == (40, 100)  # swapped for display
