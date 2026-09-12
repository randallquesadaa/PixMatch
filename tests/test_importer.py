from __future__ import annotations

import os
from datetime import datetime

from PIL import Image

from app.config import AppConfig
from app.core.geo import CountryResolver
from app.core.importer import (
    ImportStatus,
    LibraryIndex,
    apply_import,
    build_import_plan,
    is_within,
    undo_import,
)
from app.core.scanner import FileKind, ScannedFile
from app.database.history import OperationHistory

# San José, Costa Rica
CR_LAT = ((9.0, 55.0, 41.16), "N")
CR_LON = ((84.0, 5.0, 26.4), "W")


def _img(
    path, dt: datetime | None = None, *, gps=True, color=(20, 90, 160), fmt=None, size=(24, 18)
):
    path = os.fspath(path)
    img = Image.new("RGB", size, color)
    exif = img.getexif()
    if dt is not None:
        exif.get_ifd(0x8769)[0x9003] = dt.strftime("%Y:%m:%d %H:%M:%S")
    if gps:
        g = exif.get_ifd(0x8825)
        g[1], g[2] = CR_LAT[1], CR_LAT[0]
        g[3], g[4] = CR_LON[1], CR_LON[0]
    img.save(path, format=fmt, exif=exif)
    return path


def _sf(path, kind=FileKind.IMAGE) -> ScannedFile:
    st = os.stat(path)
    return ScannedFile(path=str(path), size=st.st_size, mtime=st.st_mtime, kind=kind)


def _cfg(**kw) -> AppConfig:
    return AppConfig(use_cache=False, **kw)


def _plan(source_files, library, *, config=None, index=None):
    config = config or _cfg()
    idx = index if index is not None else LibraryIndex(str(library))
    if index is None:
        idx.build([])
        idx.prepare_dims()
    return build_import_plan(
        source_files,
        library_root=str(library),
        index=idx,
        resolver=CountryResolver(),
        config=config,
    )


# -- planning --------------------------------------------------------
def test_new_image_goes_to_country_year_month(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    lib.mkdir()
    _img(src / "DSC1.jpg", datetime(2023, 5, 14, 14, 30, 5))

    plans = _plan([_sf(src / "DSC1.jpg")], lib)
    assert len(plans) == 1
    p = plans[0]
    assert p.status is ImportStatus.NEW
    assert p.country == "Costa Rica"
    assert os.path.relpath(p.dest_dir, lib) == os.path.join("Costa Rica", "2023", "2023-05")
    assert p.dest_name == "20230514_143005.jpg"


def test_image_without_gps_uses_no_location_label(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    lib.mkdir()
    _img(src / "a.jpg", datetime(2022, 1, 2, 3, 4, 5), gps=False)

    plans = _plan([_sf(src / "a.jpg")], lib, config=_cfg(import_no_location_label="Sin ubicación"))
    assert plans[0].country == "Sin ubicación"
    assert plans[0].status is ImportStatus.NEW


def test_reuses_existing_country_folder_name_case_insensitively(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    (lib / "COSTA RICA").mkdir(parents=True)
    _img(src / "a.jpg", datetime(2023, 5, 1, 0, 0, 0))

    plans = _plan([_sf(src / "a.jpg")], lib)
    assert plans[0].country == "COSTA RICA"


def test_reuses_existing_country_folder_name_ignoring_accents(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    # library already has an unaccented "Sin ubicacion" (e.g. an exFAT drive
    # organised by an older run); the default label has the accent
    (lib / "Sin ubicacion").mkdir(parents=True)
    _img(src / "a.jpg", datetime(2022, 1, 2, 3, 4, 5), gps=False)

    plans = _plan([_sf(src / "a.jpg")], lib, config=_cfg(import_no_location_label="Sin ubicación"))
    assert plans[0].country == "Sin ubicacion"
    assert os.path.relpath(plans[0].dest_dir, lib).startswith("Sin ubicacion")


def test_country_alias_is_applied(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    lib.mkdir()
    _img(src / "a.jpg", datetime(2023, 5, 1, 0, 0, 0))

    cfg = _cfg(import_country_aliases={"Costa Rica": "CR"})
    plans = _plan([_sf(src / "a.jpg")], lib, config=cfg)
    assert plans[0].country == "CR"


def test_exact_duplicate_detected(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    dest = lib / "Costa Rica" / "2023" / "2023-05"
    dest.mkdir(parents=True)
    _img(src / "a.jpg", datetime(2023, 5, 14, 14, 30, 5))
    import shutil

    shutil.copy2(src / "a.jpg", dest / "20230514_143005.jpg")

    idx = LibraryIndex(str(lib))
    idx.build([_sf(dest / "20230514_143005.jpg")])
    plans = _plan([_sf(src / "a.jpg")], lib, index=idx)
    assert plans[0].status is ImportStatus.DUPLICATE_EXACT
    assert plans[0].duplicate_of == str(dest / "20230514_143005.jpg")


def test_pixel_identical_duplicate_detected(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    libimg = lib / "Costa Rica" / "2023" / "2023-05"
    libimg.mkdir(parents=True)
    # same pixels, different container (PNG in library, BMP incoming)
    _img(libimg / "old.png", datetime(2023, 5, 14, 14, 30, 5), gps=False, fmt="PNG")
    _img(src / "new.bmp", datetime(2023, 5, 14, 14, 30, 5), fmt="BMP")

    idx = LibraryIndex(str(lib))
    idx.build([_sf(libimg / "old.png")])
    idx.prepare_dims()
    plans = _plan([_sf(src / "new.bmp")], lib, index=idx)
    assert plans[0].status is ImportStatus.DUPLICATE_PIXEL


def test_in_batch_duplicate(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    lib.mkdir()
    _img(src / "a.jpg", datetime(2023, 5, 14, 14, 30, 5))
    import shutil

    shutil.copy2(src / "a.jpg", src / "b.jpg")

    plans = _plan([_sf(src / "a.jpg"), _sf(src / "b.jpg")], lib)
    by_name = {os.path.basename(p.source_path): p for p in plans}
    assert by_name["a.jpg"].status is ImportStatus.NEW
    assert by_name["b.jpg"].status is ImportStatus.DUPLICATE_IN_BATCH


def test_name_conflict_gets_suffix(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    lib.mkdir()
    # same timestamp + country, different pixels -> second file needs a suffix
    _img(src / "a.jpg", datetime(2023, 5, 14, 14, 30, 5), color=(10, 10, 10))
    _img(src / "b.jpg", datetime(2023, 5, 14, 14, 30, 5), color=(200, 10, 10))

    plans = _plan([_sf(src / "a.jpg"), _sf(src / "b.jpg")], lib)
    names = sorted(p.dest_name for p in plans)
    # neither is left bare: both get a zero-padded suffix, matching the
    # convention already used by real libraries
    assert names == ["20230514_143005_01.jpg", "20230514_143005_02.jpg"]
    assert all(p.status is ImportStatus.NEW_SUFFIXED for p in plans)


def test_name_conflict_with_existing_bare_file_in_library(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    dest = lib / "Costa Rica" / "2023" / "2023-05"
    dest.mkdir(parents=True)
    # the library already has the bare name (from an earlier, single import);
    # it must not be touched, and the new arrivals must not collide with it
    (dest / "20230514_143005.jpg").write_bytes(b"already there")
    _img(src / "a.jpg", datetime(2023, 5, 14, 14, 30, 5), color=(10, 10, 10))
    _img(src / "b.jpg", datetime(2023, 5, 14, 14, 30, 5), color=(200, 10, 10))

    plans = _plan([_sf(src / "a.jpg"), _sf(src / "b.jpg")], lib)
    names = sorted(p.dest_name for p in plans)
    assert names == ["20230514_143005_01.jpg", "20230514_143005_02.jpg"]
    assert (dest / "20230514_143005.jpg").read_bytes() == b"already there"


# -- applying -------------------------------------------------------
def test_apply_moves_and_undo_restores(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    lib.mkdir()
    origin = _img(src / "DSC1.jpg", datetime(2023, 5, 14, 14, 30, 5))
    hist = OperationHistory(tmp_path / "h.sqlite3")

    plans = _plan([_sf(origin)], lib)
    report = apply_import(plans, history=hist)

    assert len(report.moved) == 1
    assert not os.path.exists(origin)
    moved_to = lib / "Costa Rica" / "2023" / "2023-05" / "20230514_143005.jpg"
    assert moved_to.exists()
    assert any(e.action == "import" and e.result == "ok" for e in hist.recent())

    undo = undo_import(report.moved, history=hist)
    assert len(undo.moved) == 1
    assert os.path.exists(origin)
    assert not moved_to.exists()
    hist.close()


def test_apply_leaves_source_when_destination_exists(tmp_path):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    dest = lib / "Costa Rica" / "2023" / "2023-05"
    dest.mkdir(parents=True)
    origin = _img(src / "DSC1.jpg", datetime(2023, 5, 14, 14, 30, 5), color=(1, 2, 3))
    plans = _plan([_sf(origin)], lib)
    # a different file already sits at the exact target name
    (dest / "20230514_143005.jpg").write_bytes(b"not an image, but occupies the name")

    # force the plan onto the occupied name (bypass suffix resolution)
    plans[0].dest_name = "20230514_143005.jpg"
    plans[0].status = ImportStatus.NEW
    report = apply_import(plans)
    assert report.failed
    assert os.path.exists(origin)


def test_duplicate_source_deleted_only_when_ticked(tmp_path, monkeypatch):
    src = tmp_path / "src"
    lib = tmp_path / "lib"
    src.mkdir()
    dest = lib / "Costa Rica" / "2023" / "2023-05"
    dest.mkdir(parents=True)
    origin = _img(src / "a.jpg", datetime(2023, 5, 14, 14, 30, 5))
    import shutil

    shutil.copy2(origin, dest / "20230514_143005.jpg")

    trashed: list[str] = []
    monkeypatch.setattr(
        "app.core.importer._trash", lambda p: (trashed.append(p), os.remove(p), None)[-1]
    )

    idx = LibraryIndex(str(lib))
    idx.build([_sf(dest / "20230514_143005.jpg")])
    plans = _plan([_sf(origin)], lib, index=idx)
    assert plans[0].status is ImportStatus.DUPLICATE_EXACT

    # not ticked -> nothing happens
    report = apply_import(plans)
    assert report.outcomes == []
    assert os.path.exists(origin)

    # ticked -> source goes to trash
    plans[0].delete_source_if_dup = True
    report = apply_import(plans)
    assert len(report.source_deleted) == 1
    assert trashed == [str(origin)]
    assert not os.path.exists(origin)


# -- guard ---------------------------------------------------------
def test_is_within(tmp_path):
    lib = tmp_path / "lib"
    (lib / "sub").mkdir(parents=True)
    assert is_within(str(lib / "sub"), str(lib))
    assert not is_within(str(tmp_path / "other"), str(lib))
