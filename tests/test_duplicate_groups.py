from __future__ import annotations

from app.core.duplicate_groups import (
    Decision,
    FileRecord,
    build_exact_groups,
)
from app.core.scanner import FileKind
from app.core.similarity import MatchCategory


def _rec(path, sha, size=100, mtime=1000.0, kind=FileKind.IMAGE):
    return FileRecord(path=path, size=size, mtime=mtime, kind=kind, sha256=sha)


def test_groups_only_with_two_or_more():
    recs = [
        _rec("/a.jpg", "h1"),
        _rec("/b.jpg", "h1"),
        _rec("/c.jpg", "h2"),  # unique -> no group
    ]
    groups = build_exact_groups(recs)
    assert len(groups) == 1
    assert groups[0].count == 2
    assert groups[0].category is MatchCategory.FILE_IDENTICAL


def test_multi_file_group_is_single_group():
    recs = [_rec(f"/{n}.jpg", "same") for n in "abcd"]
    groups = build_exact_groups(recs)
    assert len(groups) == 1
    assert groups[0].count == 4


def test_records_without_hash_are_ignored():
    recs = [
        FileRecord("/x.jpg", 10, 1.0, FileKind.IMAGE, sha256=None, error="unreadable"),
        FileRecord("/y.jpg", 10, 1.0, FileKind.IMAGE, sha256=None),
    ]
    assert build_exact_groups(recs) == []


def test_group_ids_ordered_by_potential_savings():
    recs = [
        _rec("/small1", "s", size=10),
        _rec("/small2", "s", size=10),
        _rec("/big1", "b", size=1_000_000),
        _rec("/big2", "b", size=1_000_000),
        _rec("/big3", "b", size=1_000_000),
    ]
    groups = build_exact_groups(recs)
    assert groups[0].group_id == 1
    assert groups[0].records[0].size == 1_000_000  # bigger savings first


def test_reclaimable_and_unsafe_flags():
    recs = [_rec("/a", "h", size=500), _rec("/b", "h", size=500)]
    g = build_exact_groups(recs)[0]
    assert g.reclaimable_bytes == 0
    g.records[0].decision = Decision.DELETE
    assert g.reclaimable_bytes == 500
    assert not g.has_unsafe_decision
    g.records[1].decision = Decision.DELETE
    assert g.has_unsafe_decision


def test_deleted_records_resolve_group_and_shrink_totals():
    recs = [_rec(f"/{n}", "h", size=1000) for n in "abc"]
    groups = build_exact_groups(recs)
    from app.core.duplicate_groups import AnalysisResult

    r = AnalysisResult("/root", False, object(), groups, hashed_files=3, elapsed_seconds=1.0)
    assert r.redundant_copies == 2 and r.reclaimable_bytes == 2000
    assert not groups[0].is_resolved

    groups[0].records[0].deleted = True
    assert r.redundant_copies == 1 and r.reclaimable_bytes == 1000
    assert not groups[0].is_resolved  # 2 active left

    groups[0].records[1].deleted = True
    assert groups[0].is_resolved  # only 1 active left
    assert r.redundant_copies == 0 and r.reclaimable_bytes == 0
    assert r.deleted_file_count == 2
    assert r.unresolved_groups == []


def test_drop_deleted_prunes_result():
    recs = [_rec(f"/{n}", "h", size=10) for n in "abcd"]
    groups = build_exact_groups(recs)
    from app.core.duplicate_groups import AnalysisResult

    r = AnalysisResult("/root", False, object(), groups, hashed_files=4, elapsed_seconds=0.0)
    groups[0].records[0].deleted = True
    groups[0].records[1].deleted = True
    removed = r.drop_deleted()
    assert removed == 2
    assert len(r.groups) == 1 and r.groups[0].count == 2


def test_analysis_result_dashboard_numbers():
    recs = [_rec(f"/{n}", "h", size=1000) for n in "abc"]
    groups = build_exact_groups(recs)
    from app.core.duplicate_groups import AnalysisResult

    r = AnalysisResult("/root", False, object(), groups, hashed_files=3, elapsed_seconds=1.0)
    assert r.identical_file_count == 3
    assert r.redundant_copies == 2
    assert r.reclaimable_bytes == 2000
