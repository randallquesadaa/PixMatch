from __future__ import annotations

import os

from app.core.duplicate_groups import DuplicateGroup, FileRecord
from app.core.recommendation import recommend_keep
from app.core.scanner import FileKind
from app.core.similarity import MatchCategory


def _rec(path, size=1000, mtime=1000.0, w=None, h=None, kind=FileKind.IMAGE):
    r = FileRecord(path=path, size=size, mtime=mtime, kind=kind)
    r.width, r.height = w, h
    r._image_info = _FakeInfo()
    return r


class _FakeInfo:
    error = None
    format = "PNG"
    mode = "RGB"
    has_exif = False
    camera_make = ""
    camera_model = ""
    date_taken = None
    orientation = 1

    @property
    def oriented_size(self):
        return (0, 0)


def _group(records, category=MatchCategory.VERY_SIMILAR):
    return DuplicateGroup(1, category, list(records))


def test_none_for_trivial_group():
    assert recommend_keep(_group([_rec("/a")])) is None


def test_prefers_higher_resolution(tmp_path):
    small = _rec(str(tmp_path / "img_small.jpg"), size=500, w=800, h=600)
    big = _rec(str(tmp_path / "img.jpg"), size=1500, w=4000, h=3000)
    r = recommend_keep(_group([small, big]))
    assert r.record is big
    assert any("resoluci" in x for x in r.reasons)
    assert r.confidence >= 60


def test_penalises_copy_name_and_backup_folder(tmp_path):
    (tmp_path / "Fotos").mkdir()
    (tmp_path / "Backup").mkdir()
    original = _rec(str(tmp_path / "Fotos" / "IMG_1234.jpg"), size=1000, mtime=100.0, w=100, h=100)
    copy = _rec(str(tmp_path / "Backup" / "IMG_1234 - copia.jpg"), size=1000, mtime=200.0, w=100, h=100)
    r = recommend_keep(_group([original, copy], MatchCategory.FILE_IDENTICAL))
    assert r.record is original
    assert any("copia" in x or "backup" in x for x in r.reasons)


def test_exact_group_without_signal_has_low_confidence(tmp_path):
    a = _rec(str(tmp_path / "a.jpg"), size=1000, mtime=1000.0, w=100, h=100)
    b = _rec(str(tmp_path / "b.jpg"), size=1000, mtime=1000.5, w=100, h=100)
    r = recommend_keep(_group([a, b], MatchCategory.FILE_IDENTICAL))
    assert r is not None
    assert r.confidence <= 55


def test_prefers_oldest_as_original(tmp_path):
    old = _rec(str(tmp_path / "a.jpg"), size=1000, mtime=1_000.0, w=100, h=100)
    new = _rec(str(tmp_path / "b.jpg"), size=1000, mtime=9_000_000.0, w=100, h=100)
    r = recommend_keep(_group([new, old], MatchCategory.FILE_IDENTICAL))
    assert r.record is old


def test_video_prefers_higher_bitrate(tmp_path):
    class V:
        error = None
        creation_time = None

        def __init__(self, br):
            self.bitrate = br
            self.duration = 10.0
            self.width = 1920
            self.height = 1080

    a = FileRecord(path=str(tmp_path / "a.mp4"), size=5_000_000, mtime=1.0, kind=FileKind.VIDEO)
    a.video_info = V(2_000_000)
    b = FileRecord(path=str(tmp_path / "b.mp4"), size=2_000_000, mtime=1.0, kind=FileKind.VIDEO)
    b.video_info = V(800_000)
    r = recommend_keep(_group([b, a], MatchCategory.VERY_SIMILAR))
    assert r.record is a
    assert any("bitrate" in x for x in r.reasons)


def test_deleted_records_are_ignored(tmp_path):
    keep = _rec(str(tmp_path / "keep.jpg"), size=1000, w=4000, h=3000)
    gone = _rec(str(tmp_path / "gone.jpg"), size=2000, w=8000, h=6000)
    gone.deleted = True
    assert recommend_keep(_group([keep, gone])) is None  # only 1 active left
