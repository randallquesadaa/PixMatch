from __future__ import annotations

from app.database.database import FileCacheDB
from app.database.models import CachedFile


def _db(tmp_path):
    return FileCacheDB(tmp_path / "cache.sqlite3")


def test_upsert_and_get(tmp_path):
    db = _db(tmp_path)
    db.upsert_many([
        CachedFile(path="/a.jpg", size=100, mtime=1000.0, sha256="deadbeef"),
        CachedFile(path="/b.jpg", size=200, mtime=2000.0, phash="00ff00ff00ff00ff"),
    ])
    got = db.get_many(["/a.jpg", "/b.jpg", "/missing.jpg"])
    assert set(got) == {"/a.jpg", "/b.jpg"}
    assert got["/a.jpg"].sha256 == "deadbeef"
    assert got["/b.jpg"].phash == "00ff00ff00ff00ff"
    db.close()


def test_upsert_replaces_existing(tmp_path):
    db = _db(tmp_path)
    db.upsert_many([CachedFile(path="/a", size=1, mtime=1.0, sha256="old")])
    db.upsert_many([CachedFile(path="/a", size=2, mtime=2.0, sha256="new")])
    row = db.get_many(["/a"])["/a"]
    assert (row.size, row.sha256) == (2, "new")
    db.close()


def test_validity_check():
    row = CachedFile(path="/a", size=100, mtime=1234.5678, sha256="x")
    assert row.is_valid_for(100, 1234.5678)
    assert row.is_valid_for(100, 1234.5679)          # within tolerance
    assert not row.is_valid_for(101, 1234.5678)      # size changed
    assert not row.is_valid_for(100, 9999.0)         # mtime changed


def test_persistence_across_reopen(tmp_path):
    db = _db(tmp_path)
    db.upsert_many([CachedFile(path="/a", size=1, mtime=1.0, sha256="keep")])
    db.close()

    db2 = _db(tmp_path)
    assert db2.get_many(["/a"])["/a"].sha256 == "keep"
    assert db2.count() == 1
    db2.close()


def test_prune_missing(tmp_path):
    db = _db(tmp_path)
    db.upsert_many([
        CachedFile(path="/a", size=1, mtime=1.0),
        CachedFile(path="/b", size=1, mtime=1.0),
        CachedFile(path="/c", size=1, mtime=1.0),
    ])
    removed = db.prune_missing(["/a", "/c"])
    assert removed == 1
    assert set(db.get_many(["/a", "/b", "/c"])) == {"/a", "/c"}
    db.close()


def test_clear(tmp_path):
    db = _db(tmp_path)
    db.upsert_many([CachedFile(path="/a", size=1, mtime=1.0)])
    db.clear()
    assert db.count() == 0
    db.close()


def test_corrupt_db_file_degrades_gracefully(tmp_path):
    bad = tmp_path / "bad.sqlite3"
    bad.write_bytes(b"this is not sqlite" * 10)
    db = FileCacheDB(bad)
    # Either it could not open (not available) or it recovered - in both cases
    # the API must not raise.
    assert db.get_many(["/x"]) == {}
    db.upsert_many([CachedFile(path="/x", size=1, mtime=1.0)])
    db.close()
