from __future__ import annotations

import errno
import os
from datetime import datetime

from PIL import Image

from app.config import AppConfig
from app.core.geo import CountryResolver
from app.core.organizer import (
    OrganizeStatus,
    apply_organize,
    build_organize_plan,
    undo_organize,
)
from app.core.scanner import FileKind, ScannedFile
from app.database.history import OperationHistory

# San José, Costa Rica
CR_LAT = ((9.0, 55.0, 41.16), "N")
CR_LON = ((84.0, 5.0, 26.4), "W")


def _img(path, dt: datetime | None = None, *, gps=True, color=(20, 90, 160), size=(24, 18)):
    path = os.fspath(path)
    img = Image.new("RGB", size, color)
    exif = img.getexif()
    if dt is not None:
        exif.get_ifd(0x8769)[0x9003] = dt.strftime("%Y:%m:%d %H:%M:%S")
    if gps:
        g = exif.get_ifd(0x8825)
        g[1], g[2] = CR_LAT[1], CR_LAT[0]
        g[3], g[4] = CR_LON[1], CR_LON[0]
    img.save(path, exif=exif)
    return path


def _sf(path, kind=FileKind.IMAGE) -> ScannedFile:
    st = os.stat(path)
    return ScannedFile(path=str(path), size=st.st_size, mtime=st.st_mtime, kind=kind)


def _cfg(**kw) -> AppConfig:
    return AppConfig(use_cache=False, **kw)


def _plan(source_files, root, *, config=None):
    return build_organize_plan(
        source_files,
        root=str(root),
        resolver=CountryResolver(),
        config=config or _cfg(),
    )


def test_new_image_moves_into_country_year_month(tmp_path):
    root = tmp_path / "old_drive"
    (root / "random_dump").mkdir(parents=True)
    origin = _img(root / "random_dump" / "IMG_0001.jpg", datetime(2023, 5, 14, 14, 30, 5))

    plans = _plan([_sf(origin)], root)
    assert len(plans) == 1
    p = plans[0]
    assert p.status is OrganizeStatus.MOVE
    assert p.country == "Costa Rica"
    assert os.path.relpath(p.dest_dir, root) == os.path.join("Costa Rica", "2023", "2023-05")
    assert p.dest_name == "20230514_143005.jpg"
    assert p.will_move


def test_file_already_in_place_is_unchanged(tmp_path):
    root = tmp_path / "lib"
    dest = root / "Costa Rica" / "2023" / "2023-05"
    dest.mkdir(parents=True)
    origin = _img(dest / "20230514_143005.jpg", datetime(2023, 5, 14, 14, 30, 5))

    plans = _plan([_sf(origin)], root)
    assert plans[0].status is OrganizeStatus.UNCHANGED
    assert not plans[0].will_move


def test_already_suffixed_burst_in_place_is_unchanged(tmp_path):
    """A burst that already sits, correctly suffixed, in its destination
    folder must resolve back to its own names -- not get bumped to a higher
    suffix just because its own current name still occupies the slot, and
    not oscillate between suffix numbers on repeated runs."""
    root = tmp_path / "lib"
    dest = root / "Costa Rica" / "2023" / "2023-05"
    dest.mkdir(parents=True)
    dt = datetime(2023, 5, 14, 14, 30, 5)
    a = _img(dest / "20230514_143005_01.jpg", dt, color=(10, 10, 10))
    b = _img(dest / "20230514_143005_02.jpg", dt, color=(200, 10, 10))

    plans = _plan([_sf(a), _sf(b)], root)
    assert {p.status for p in plans} == {OrganizeStatus.UNCHANGED}
    assert sorted(p.dest_name for p in plans) == [
        "20230514_143005_01.jpg",
        "20230514_143005_02.jpg",
    ]

    # running it again must not change anything either (no oscillation)
    plans_again = _plan([_sf(a), _sf(b)], root)
    assert sorted(p.dest_name for p in plans_again) == sorted(p.dest_name for p in plans)


def test_no_dedup_evaluation_moves_every_identical_copy(tmp_path):
    """The organiser never compares content: two byte-identical files (same
    EXIF, since one is a plain copy of the other) are never rejected as
    duplicates -- there is no such status here -- they both get placed, only
    kept apart by the usual naming suffix."""
    root = tmp_path / "old_drive"
    root.mkdir()
    a = _img(root / "a.jpg", datetime(2021, 1, 1, 0, 0, 0), color=(5, 5, 5))
    import shutil

    b = root / "copy_of_a.jpg"
    shutil.copy2(a, b)

    plans = _plan([_sf(a), _sf(b)], root)
    assert {p.status for p in plans} == {OrganizeStatus.MOVE_SUFFIXED}
    assert len({p.dest_path for p in plans}) == 2  # suffix keeps both, neither is dropped


def test_name_conflict_gets_suffix(tmp_path):
    root = tmp_path / "old_drive"
    root.mkdir()
    a = _img(root / "a.jpg", datetime(2023, 5, 14, 14, 30, 5), color=(10, 10, 10))
    b = _img(root / "b.jpg", datetime(2023, 5, 14, 14, 30, 5), color=(200, 10, 10))

    plans = _plan([_sf(a), _sf(b)], root)
    names = sorted(p.dest_name for p in plans)
    assert names == ["20230514_143005_01.jpg", "20230514_143005_02.jpg"]
    assert all(p.status is OrganizeStatus.MOVE_SUFFIXED for p in plans)


def test_apply_moves_and_undo_restores(tmp_path):
    root = tmp_path / "old_drive"
    root.mkdir()
    origin = _img(root / "IMG_0001.jpg", datetime(2023, 5, 14, 14, 30, 5))
    hist = OperationHistory(tmp_path / "h.sqlite3")

    plans = _plan([_sf(origin)], root)
    report = apply_organize(plans, history=hist)

    assert len(report.moved) == 1
    assert not os.path.exists(origin)
    moved_to = root / "Costa Rica" / "2023" / "2023-05" / "20230514_143005.jpg"
    assert moved_to.exists()
    assert any(e.action == "organize" and e.result == "ok" for e in hist.recent())

    undo = undo_organize(report.moved, history=hist)
    assert len(undo.moved) == 1
    assert os.path.exists(origin)
    assert not moved_to.exists()
    hist.close()


def test_unchanged_file_is_left_alone_on_apply(tmp_path):
    root = tmp_path / "lib"
    dest = root / "Costa Rica" / "2023" / "2023-05"
    dest.mkdir(parents=True)
    origin = _img(dest / "20230514_143005.jpg", datetime(2023, 5, 14, 14, 30, 5))
    before = os.path.getmtime(origin)

    plans = _plan([_sf(origin)], root)
    report = apply_organize(plans)
    assert report.moved == []
    assert os.path.exists(origin)
    assert os.path.getmtime(origin) == before


def test_cross_device_fallback_still_moves_the_file(tmp_path, monkeypatch):
    """If the fast rename raises EXDEV (a different filesystem), the copy ->
    verify -> remove fallback still gets the file to the right place."""
    root = tmp_path / "old_drive"
    root.mkdir()
    origin = _img(root / "IMG_0001.jpg", datetime(2023, 5, 14, 14, 30, 5))

    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        # only the very first call (the organiser's own fast path) fails;
        # verified_copy_move's own internal os.replace must still work
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)

    plans = _plan([_sf(origin)], root)
    report = apply_organize(plans)
    assert len(report.moved) == 1
    assert not os.path.exists(origin)
    moved_to = root / "Costa Rica" / "2023" / "2023-05" / "20230514_143005.jpg"
    assert moved_to.exists()
