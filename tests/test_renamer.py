from __future__ import annotations

import os
from datetime import datetime

from PIL import Image

from app.core.renamer import (
    DateSource,
    RenameStatus,
    apply_renames,
    build_rename_plan,
    capture_datetime,
    undo_renames,
)
from app.core.scanner import FileKind


class _F:
    """Minimal stand-in for ScannedFile."""

    def __init__(self, path, kind=FileKind.IMAGE):
        self.path = str(path)
        self.kind = kind
        self.mtime = os.stat(path).st_mtime


def _jpeg(path, dt: datetime | None = None, size=(20, 16)):
    path = os.fspath(path)
    img = Image.new("RGB", size, (10, 120, 200))
    if dt is not None:
        exif = img.getexif()
        exif[0x0132] = dt.strftime("%Y:%m:%d %H:%M:%S")  # DateTime
        sub = exif.get_ifd(0x8769)
        sub[0x9003] = dt.strftime("%Y:%m:%d %H:%M:%S")  # DateTimeOriginal
        img.save(path, exif=exif)
    else:
        img.save(path)
    return path


# -- date extraction -----------------------------------------------
def test_reads_exif_datetime(tmp_path):
    p = _jpeg(tmp_path / "x.jpg", datetime(2022, 12, 2, 14, 30, 5))
    dt, src = capture_datetime(p, "image", os.stat(p).st_mtime)
    assert dt == datetime(2022, 12, 2, 14, 30, 5)
    assert src is DateSource.EXIF_ORIGINAL


def test_falls_back_to_mtime(tmp_path):
    p = _jpeg(tmp_path / "x.jpg")
    os.utime(p, (1_000_000_000, 1_600_000_000))
    dt, src = capture_datetime(p, "image", os.stat(p).st_mtime)
    assert src is DateSource.FILE_MTIME
    assert dt == datetime.fromtimestamp(1_600_000_000)


# -- planning -----------------------------------------------------
def test_plan_names_and_unchanged(tmp_path):
    _jpeg(tmp_path / "DSC01.jpg", datetime(2022, 12, 2, 14, 30, 5))
    _jpeg(tmp_path / "20230101_090000.jpg", datetime(2023, 1, 1, 9, 0, 0))
    plans = build_rename_plan([_F(tmp_path / "DSC01.jpg"), _F(tmp_path / "20230101_090000.jpg")])
    by_old = {p.old_name: p for p in plans}
    assert by_old["DSC01.jpg"].new_name == "20221202_143005.jpg"
    assert by_old["DSC01.jpg"].status is RenameStatus.RENAME
    assert by_old["20230101_090000.jpg"].status is RenameStatus.UNCHANGED


def test_conflict_gets_suffix(tmp_path):
    # two photos, same second -> the second gets _2
    _jpeg(tmp_path / "a.jpg", datetime(2022, 12, 2, 14, 30, 5))
    _jpeg(tmp_path / "b.jpg", datetime(2022, 12, 2, 14, 30, 5))
    plans = build_rename_plan([_F(tmp_path / "a.jpg"), _F(tmp_path / "b.jpg")])
    names = sorted(p.new_name for p in plans)
    assert names == ["20221202_143005.jpg", "20221202_143005_2.jpg"]
    assert any(p.status is RenameStatus.SUFFIXED for p in plans)


def test_conflict_with_existing_file_on_disk(tmp_path):
    _jpeg(tmp_path / "20221202_143005.jpg")  # already there, not in the plan
    _jpeg(tmp_path / "raw.jpg", datetime(2022, 12, 2, 14, 30, 5))
    plans = build_rename_plan([_F(tmp_path / "raw.jpg")])
    assert plans[0].new_name == "20221202_143005_2.jpg"


def test_lowercase_and_jpeg_normalisation(tmp_path):
    _jpeg(tmp_path / "P.JPEG", datetime(2022, 1, 1, 0, 0, 0))
    plans = build_rename_plan([_F(tmp_path / "P.JPEG")])
    assert plans[0].new_name == "20220101_000000.jpg"


def test_prefix(tmp_path):
    _jpeg(tmp_path / "a.jpg", datetime(2022, 1, 1, 0, 0, 0))
    plans = build_rename_plan([_F(tmp_path / "a.jpg")], prefix="IMG_")
    assert plans[0].new_name == "IMG_20220101_000000.jpg"


def test_subfolders_number_independently(tmp_path):
    (tmp_path / "d1").mkdir()
    (tmp_path / "d2").mkdir()
    _jpeg(tmp_path / "d1" / "a.jpg", datetime(2022, 1, 1, 0, 0, 0))
    _jpeg(tmp_path / "d2" / "b.jpg", datetime(2022, 1, 1, 0, 0, 0))
    plans = build_rename_plan([_F(tmp_path / "d1" / "a.jpg"), _F(tmp_path / "d2" / "b.jpg")])
    # same target name is fine - different directories
    assert all(p.new_name == "20220101_000000.jpg" for p in plans)


# -- applying ---------------------------------------------------
def test_apply_renames_on_disk(tmp_path):
    _jpeg(tmp_path / "raw.jpg", datetime(2022, 12, 2, 14, 30, 5))
    plans = build_rename_plan([_F(tmp_path / "raw.jpg")])
    report = apply_renames(plans)
    assert len(report.succeeded) == 1
    assert not (tmp_path / "raw.jpg").exists()
    assert (tmp_path / "20221202_143005.jpg").exists()


def test_apply_handles_a_swap(tmp_path):
    _jpeg(tmp_path / "20220102_000000.jpg", datetime(2022, 1, 1, 0, 0, 0))
    _jpeg(tmp_path / "20220101_000000.jpg", datetime(2022, 1, 2, 0, 0, 0))
    plans = build_rename_plan(
        [
            _F(tmp_path / "20220102_000000.jpg"),
            _F(tmp_path / "20220101_000000.jpg"),
        ]
    )
    report = apply_renames(plans)
    assert len(report.succeeded) == 2 and not report.failed
    a = Image.open(tmp_path / "20220101_000000.jpg").getexif().get_ifd(0x8769)[0x9003]
    assert a == "2022:01:01 00:00:00"


def test_apply_never_overwrites(tmp_path):
    _jpeg(tmp_path / "20221202_143005.jpg", datetime(2020, 1, 1, 0, 0, 0))  # occupied
    _jpeg(tmp_path / "raw.jpg", datetime(2022, 12, 2, 14, 30, 5))
    # force a plan that would collide (bypass conflict resolution)
    from app.core.renamer import RenamePlan

    p = RenamePlan(
        path=str(tmp_path / "raw.jpg"),
        directory=str(tmp_path),
        old_name="raw.jpg",
        new_name="20221202_143005.jpg",
        source=DateSource.EXIF_ORIGINAL,
        timestamp=datetime(2022, 12, 2, 14, 30, 5),
        status=RenameStatus.RENAME,
    )
    report = apply_renames([p])
    assert report.failed
    assert (tmp_path / "raw.jpg").exists()  # rolled back
    assert Image.open(tmp_path / "20221202_143005.jpg").getexif()  # untouched


def test_undo(tmp_path):
    _jpeg(tmp_path / "raw.jpg", datetime(2022, 12, 2, 14, 30, 5))
    plans = build_rename_plan([_F(tmp_path / "raw.jpg")])
    report = apply_renames(plans)
    assert (tmp_path / "20221202_143005.jpg").exists()

    undo = undo_renames(report.succeeded)
    assert len(undo.succeeded) == 1
    assert (tmp_path / "raw.jpg").exists()
    assert not (tmp_path / "20221202_143005.jpg").exists()


def test_unicode_paths(tmp_path):
    d = tmp_path / "Cámara ☀"
    d.mkdir()
    _jpeg(d / "señal.jpg", datetime(2021, 6, 15, 8, 5, 30))
    plans = build_rename_plan([_F(d / "señal.jpg")])
    apply_renames(plans)
    assert (d / "20210615_080530.jpg").exists()


def test_disabled_plans_are_skipped(tmp_path):
    _jpeg(tmp_path / "raw.jpg", datetime(2022, 12, 2, 14, 30, 5))
    plans = build_rename_plan([_F(tmp_path / "raw.jpg")])
    plans[0].enabled = False
    report = apply_renames(plans)
    assert report.outcomes == []
    assert (tmp_path / "raw.jpg").exists()
