"""Persistent history of destructive operations.

Every attempt to trash or permanently delete a file is recorded here - both
successes and failures - so the user can always review "what did this app do
to my files" from Settings -> Historial.

Stored in its own SQLite file (not the analysis cache) so clearing the cache
never wipes the audit trail.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.utils.logging_setup import get_logger
from app.utils.paths import data_dir

log = get_logger(__name__)

_CREATE = """
CREATE TABLE IF NOT EXISTS operations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT    NOT NULL,
    path      TEXT    NOT NULL,
    action    TEXT    NOT NULL,   -- 'trash' | 'permanent'
    result    TEXT    NOT NULL,   -- 'ok' | 'error'
    detail    TEXT,
    size      INTEGER,
    group_id  INTEGER,
    category  TEXT
);
CREATE INDEX IF NOT EXISTS ix_operations_ts ON operations(ts);
"""


@dataclass(slots=True)
class OperationEntry:
    ts: str
    path: str
    action: str
    result: str
    detail: str | None = None
    size: int | None = None
    group_id: int | None = None
    category: str | None = None

    @staticmethod
    def now(**kw) -> OperationEntry:
        return OperationEntry(ts=datetime.now().isoformat(timespec="seconds"), **kw)


class OperationHistory:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = str(path or (data_dir() / "operations-history.sqlite3"))
        self._conn: sqlite3.Connection | None = None
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(_CREATE)
            self._conn.commit()
        except sqlite3.Error as exc:
            log.warning("No se pudo abrir el historial (%s): %s", self.path, exc)
            self._conn = None

    @property
    def available(self) -> bool:
        return self._conn is not None

    def record(self, entry: OperationEntry) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "INSERT INTO operations (ts, path, action, result, detail, size, group_id, category) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.ts,
                    entry.path,
                    entry.action,
                    entry.result,
                    entry.detail,
                    entry.size,
                    entry.group_id,
                    entry.category,
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            log.warning("No se pudo registrar la operación: %s", exc)

    def record_many(self, entries: list[OperationEntry]) -> None:
        for e in entries:
            self.record(e)

    def recent(self, limit: int = 500) -> list[OperationEntry]:
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                "SELECT ts, path, action, result, detail, size, group_id, category "
                "FROM operations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [OperationEntry(**dict(r)) for r in rows]
        except sqlite3.Error:
            return []

    def count(self) -> int:
        if self._conn is None:
            return 0
        try:
            return int(self._conn.execute("SELECT COUNT(*) FROM operations").fetchone()[0])
        except sqlite3.Error:
            return 0

    def clear(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute("DELETE FROM operations")
            self._conn.commit()
        except sqlite3.Error as exc:
            log.warning("No se pudo limpiar el historial: %s", exc)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
