from __future__ import annotations

from app.database.history import OperationEntry, OperationHistory


def test_record_and_recent(tmp_path):
    h = OperationHistory(tmp_path / "h.sqlite3")
    h.record(OperationEntry.now(path="/a.jpg", action="trash", result="ok", size=100))
    h.record(OperationEntry.now(path="/b.jpg", action="permanent", result="error",
                                detail="boom", size=50))
    rows = h.recent()
    assert len(rows) == 2
    assert rows[0].path == "/b.jpg" and rows[0].result == "error" and rows[0].detail == "boom"
    assert rows[1].path == "/a.jpg"
    assert h.count() == 2
    h.close()


def test_persist_and_clear(tmp_path):
    db = tmp_path / "h.sqlite3"
    h = OperationHistory(db)
    h.record_many([
        OperationEntry.now(path=f"/f{i}", action="trash", result="ok") for i in range(5)
    ])
    h.close()

    h2 = OperationHistory(db)
    assert h2.count() == 5
    h2.clear()
    assert h2.count() == 0
    h2.close()


def test_now_sets_timestamp():
    e = OperationEntry.now(path="/x", action="trash", result="ok")
    assert e.ts and "T" in e.ts


def test_unwritable_path_degrades(tmp_path):
    # a directory where a file is expected -> open fails, methods no-op
    bad = tmp_path / "adir"
    bad.mkdir()
    h = OperationHistory(bad)
    assert not h.available
    h.record(OperationEntry.now(path="/x", action="trash", result="ok"))
    assert h.recent() == []
    assert h.count() == 0
