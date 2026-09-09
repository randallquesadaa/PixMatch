"""Safe-deletion tests. Trash mode is exercised with a fake trash directory so
the real recycle bin is never touched."""
from __future__ import annotations

import os

import pytest

from app.core.deletion_manager import (
    DeletionManager,
    DeletionMode,
    PlannedDeletion,
    build_preview,
    build_preview_for,
)
from app.core.duplicate_groups import Decision, DuplicateGroup, FileRecord
from app.core.scanner import FileKind
from app.core.similarity import MatchCategory
from app.database.history import OperationHistory


def _record(path) -> FileRecord:
    st = os.stat(path)
    return FileRecord(path=str(path), size=st.st_size, mtime=st.st_mtime, kind=FileKind.IMAGE)


def _group(records, category=MatchCategory.FILE_IDENTICAL) -> DuplicateGroup:
    return DuplicateGroup(group_id=1, category=category, records=list(records))


def _history(tmp_path) -> OperationHistory:
    return OperationHistory(tmp_path / "hist.sqlite3")


@pytest.fixture
def fake_trash(tmp_path, monkeypatch):
    trash = tmp_path / "_trash"
    trash.mkdir()
    moved = []

    def _fake(path):
        import shutil

        dest = trash / os.path.basename(path)
        shutil.move(path, dest)
        moved.append(str(dest))

    monkeypatch.setattr("app.core.deletion_manager._send_to_trash", _fake)
    return trash, moved


# ---------------------------------------------------------------------------
def test_permanent_deletion_removes_file(tmp_path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"x" * 100)
    b.write_bytes(b"x" * 100)
    ra, rb = _record(a), _record(b)
    rb.decision = Decision.DELETE
    group = _group([ra, rb])

    preview = build_preview([group])
    assert preview.count == 1
    hist = _history(tmp_path)
    report = DeletionManager(hist).delete(preview.items, mode=DeletionMode.PERMANENT)

    assert not b.exists()
    assert a.exists()
    assert len(report.succeeded) == 1
    assert report.freed_bytes == 100
    assert rb.deleted is True
    assert hist.count() == 1
    assert hist.recent()[0].result == "ok"


def test_trash_mode_moves_not_deletes(tmp_path, fake_trash):
    trash, moved = fake_trash
    a = tmp_path / "keep.jpg"
    b = tmp_path / "dup.jpg"
    a.write_bytes(b"y" * 50)
    b.write_bytes(b"y" * 50)
    ra, rb = _record(a), _record(b)
    rb.decision = Decision.DELETE

    report = DeletionManager(_history(tmp_path)).delete(
        build_preview([_group([ra, rb])]).items, mode=DeletionMode.TRASH
    )
    assert report.succeeded
    assert not b.exists()
    assert (trash / "dup.jpg").exists()   # recoverable
    assert moved == [str(trash / "dup.jpg")]


def test_missing_file_is_reported_not_fatal(tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"z" * 10)
    rec = _record(a)
    rec.decision = Decision.DELETE
    a.unlink()  # vanished after the scan

    report = DeletionManager(_history(tmp_path)).delete(
        [PlannedDeletion(rec, 1, "x")], mode=DeletionMode.PERMANENT
    )
    assert report.failed and "no existe" in report.failed[0].error.lower()
    assert rec.deleted is False


def test_file_changed_since_scan_is_skipped(tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"z" * 10)
    rec = _record(a)
    rec.decision = Decision.DELETE
    a.write_bytes(b"z" * 999)  # size changed -> unsafe to trust the plan

    report = DeletionManager(_history(tmp_path)).delete(
        [PlannedDeletion(rec, 1, "x")], mode=DeletionMode.PERMANENT
    )
    assert report.failed and "cambió" in report.failed[0].error
    assert a.exists()


def test_directory_is_never_deleted(tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()
    rec = FileRecord(path=str(d), size=0, mtime=os.stat(d).st_mtime, kind=FileKind.OTHER)
    rec.decision = Decision.DELETE
    report = DeletionManager(_history(tmp_path)).delete(
        [PlannedDeletion(rec, 1, "x")], mode=DeletionMode.PERMANENT
    )
    assert report.failed
    assert d.exists()


def test_preview_flags_group_left_empty_and_warnings(tmp_path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"q" * 20)
    b.write_bytes(b"q" * 20)
    ra, rb = _record(a), _record(b)
    ra.decision = Decision.DELETE
    rb.decision = Decision.DELETE
    missing = FileRecord(path=str(tmp_path / "ghost.jpg"), size=1, mtime=1.0, kind=FileKind.IMAGE)
    missing.decision = Decision.DELETE

    preview = build_preview([_group([ra, rb, missing])])
    assert preview.groups_left_empty == [1]
    assert any("no existe" in w.message.lower() for w in preview.warnings)


def test_cancel_stops_batch(tmp_path):
    recs = []
    for i in range(6):
        p = tmp_path / f"f{i}.jpg"
        p.write_bytes(b"a" * 10)
        r = _record(p)
        r.decision = Decision.DELETE
        recs.append(PlannedDeletion(r, 1, "x"))

    calls = {"n": 0}

    def check():
        calls["n"] += 1
        return calls["n"] <= 3   # allow 3, then cancel

    report = DeletionManager(_history(tmp_path)).delete(
        recs, mode=DeletionMode.PERMANENT, check=check
    )
    assert report.cancelled
    assert len(report.outcomes) == 3


def test_unicode_paths(tmp_path):
    p = tmp_path / "álbum ☂" / "señal (copia).jpg"
    p.parent.mkdir()
    p.write_bytes(b"u" * 12)
    rec = _record(p)
    rec.decision = Decision.DELETE
    report = DeletionManager(_history(tmp_path)).delete(
        [PlannedDeletion(rec, 1, "x")], mode=DeletionMode.PERMANENT
    )
    assert report.succeeded and not p.exists()


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX perms, non-root")
def test_permission_error_recorded(tmp_path):
    d = tmp_path / "locked"
    d.mkdir()
    f = d / "a.jpg"
    f.write_bytes(b"x" * 10)
    rec = _record(f)
    rec.decision = Decision.DELETE
    os.chmod(d, 0o500)  # can read/list but not delete children
    try:
        report = DeletionManager(_history(tmp_path)).delete(
            [PlannedDeletion(rec, 1, "x")], mode=DeletionMode.PERMANENT
        )
        assert report.failed
        assert f.exists()
    finally:
        os.chmod(d, 0o700)


def test_build_preview_for_single_file(tmp_path):
    a = tmp_path / "a.jpg"
    b = tmp_path / "b.jpg"
    a.write_bytes(b"q" * 30)
    b.write_bytes(b"q" * 30)
    ra, rb = _record(a), _record(b)
    group = _group([ra, rb])

    # deleting one of two -> the other survives, no "empty group" warning
    p = build_preview_for([(rb, group)], rb)
    assert p.count == 1 and p.total_bytes == 30
    assert p.groups_left_empty == []

    # the other one is already marked for deletion -> deleting this one empties it
    ra.decision = Decision.DELETE
    p2 = build_preview_for([(rb, group)], rb)
    assert p2.groups_left_empty == [1]


def test_build_preview_for_skips_already_deleted(tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"x" * 10)
    rec = _record(a)
    rec.deleted = True
    assert build_preview_for([(rec, _group([rec, _record(a)]))]).count == 0


def test_history_records_failures_too(tmp_path):
    rec = FileRecord(path=str(tmp_path / "nope.jpg"), size=1, mtime=1.0, kind=FileKind.IMAGE)
    rec.decision = Decision.DELETE
    hist = _history(tmp_path)
    DeletionManager(hist).delete(
        [PlannedDeletion(rec, 3, "Archivo idéntico")], mode=DeletionMode.TRASH
    )
    entry = hist.recent()[0]
    assert entry.result == "error"
    assert entry.action == "trash"
    assert entry.group_id == 3
