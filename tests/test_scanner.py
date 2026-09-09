from __future__ import annotations

import os
import sys

import pytest

from app.core.scanner import FileKind, Scanner, ScanOptions, classify_extension


def test_classify_extension():
    assert classify_extension("PHOTO.JPG") is FileKind.IMAGE
    assert classify_extension("clip.MKV") is FileKind.VIDEO
    assert classify_extension("notes.txt") is FileKind.OTHER


def _touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_recursive_scan_counts(tmp_path):
    _touch(tmp_path / "a.jpg", b"a")
    _touch(tmp_path / "sub1" / "b.png", b"bb")
    _touch(tmp_path / "sub1" / "sub2" / "c.mp4", b"ccc")
    _touch(tmp_path / "sub1" / "sub2" / "readme.txt", b"dddd")

    files = []
    stats = Scanner(
        tmp_path,
        ScanOptions(include_kinds=frozenset({FileKind.IMAGE, FileKind.VIDEO, FileKind.OTHER})),
    ).scan(on_file=files.append)

    assert stats.files_found == 4
    assert stats.images == 2
    assert stats.videos == 1
    assert stats.others == 1
    assert stats.total_size == 1 + 2 + 3 + 4
    assert stats.folders_scanned == 3
    assert {os.path.basename(f.path) for f in files} == {"a.jpg", "b.png", "c.mp4", "readme.txt"}


def test_unicode_and_spaces(tmp_path):
    _touch(tmp_path / "vacaciones 2025" / "café ☕" / "Imagén Ñ.jpg", b"data")
    files = []
    stats = Scanner(tmp_path).scan(on_file=files.append)
    assert stats.images == 1
    assert files[0].path.endswith("Imagén Ñ.jpg")


def test_min_size_and_ignored_ext(tmp_path):
    _touch(tmp_path / "tiny.jpg", b"x")          # 1 byte
    _touch(tmp_path / "big.jpg", b"x" * 5000)
    _touch(tmp_path / "skip.aae", b"x" * 5000)

    opts = ScanOptions(min_size_bytes=100, ignored_extensions=ScanOptions.normalise_ext(["aae"]))
    files = []
    Scanner(tmp_path, opts).scan(on_file=files.append)
    assert [os.path.basename(f.path) for f in files] == ["big.jpg"]


def test_excluded_dir_name(tmp_path):
    _touch(tmp_path / "keep.jpg", b"x" * 200)
    _touch(tmp_path / "node_modules" / "pkg" / "logo.png", b"x" * 200)
    opts = ScanOptions(excluded_dir_names=frozenset({"node_modules"}))
    files = []
    stats = Scanner(tmp_path, opts).scan(on_file=files.append)
    assert [os.path.basename(f.path) for f in files] == ["keep.jpg"]
    assert stats.excluded_folders == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_symlink_cycle_is_safe(tmp_path):
    (tmp_path / "real").mkdir()
    _touch(tmp_path / "real" / "img.jpg", b"x" * 50)
    os.symlink(tmp_path / "real", tmp_path / "real" / "loop", target_is_directory=True)
    os.symlink(tmp_path, tmp_path / "real" / "up", target_is_directory=True)

    files = []
    stats = Scanner(tmp_path).scan(on_file=files.append)  # must terminate
    assert stats.images == 1


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX perms, non-root")
def test_permission_error_is_recorded_not_fatal(tmp_path):
    good = tmp_path / "good"
    bad = tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    _touch(good / "a.jpg", b"x" * 100)
    _touch(bad / "secret.jpg", b"x" * 100)
    os.chmod(bad, 0o000)
    try:
        files = []
        stats = Scanner(tmp_path).scan(on_file=files.append)
        assert stats.images == 1
        assert any("bad" in issue.path for issue in stats.issues)
    finally:
        os.chmod(bad, 0o755)


def test_missing_root(tmp_path):
    stats = Scanner(tmp_path / "does-not-exist").scan()
    assert stats.issues and stats.files_found == 0


def test_cancellation_stops_scan(tmp_path):
    for i in range(50):
        _touch(tmp_path / f"d{i}" / "f.jpg", b"x" * 10)
    from app.utils.control import RunController

    ctrl = RunController()
    ctrl.cancel()
    stats = Scanner(tmp_path, controller=ctrl).scan()
    assert stats.files_found == 0
