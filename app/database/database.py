"""SQLite-backed analysis cache.

A single :class:`FileCacheDB` instance is created and used **by one thread**
(the analysis worker): reads happen up-front, writes happen at the end, so no
cross-thread SQLite access is needed.

If the database file is missing, unreadable or corrupt, every method degrades
to a no-op / empty result and logs a warning - the analysis simply runs
without a cache rather than failing.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Sequence

from app.database.models import CREATE_SQL, SCHEMA_VERSION, CachedFile
from app.utils.logging_setup import get_logger

log = get_logger(__name__)

_INSERT_SQL = (
    "INSERT INTO file_cache (%s) VALUES (%s) "
    "ON CONFLICT(path) DO UPDATE SET %s"
    % (
        ", ".join(CachedFile.fields()),
        ", ".join("?" for _ in CachedFile.fields()),
        ", ".join(f"{f}=excluded.{f}" for f in CachedFile.fields() if f != "path"),
    )
)


class FileCacheDB:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn: sqlite3.Connection | None = None
        self._open()

    # ------------------------------------------------------------------
    def _open(self) -> None:
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, timeout=30)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(CREATE_SQL)
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            elif int(row["value"]) != SCHEMA_VERSION:
                log.warning(
                    "Cache schema %s != %s - rebuilding cache.",
                    row["value"], SCHEMA_VERSION,
                )
                self._conn.execute("DROP TABLE IF EXISTS file_cache")
                self._conn.executescript(CREATE_SQL)
                self._conn.execute(
                    "UPDATE meta SET value=? WHERE key='schema_version'",
                    (str(SCHEMA_VERSION),),
                )
            self._conn.commit()
        except sqlite3.Error as exc:
            log.warning("No se pudo abrir la caché (%s): %s", self.path, exc)
            self._conn = None

    @property
    def available(self) -> bool:
        return self._conn is not None

    # ------------------------------------------------------------------
    def get_many(self, paths: Iterable[str]) -> dict[str, CachedFile]:
        if self._conn is None:
            return {}
        paths = list(paths)
        out: dict[str, CachedFile] = {}
        try:
            cur = self._conn.cursor()
            for chunk_start in range(0, len(paths), 400):
                chunk = paths[chunk_start:chunk_start + 400]
                q = "SELECT * FROM file_cache WHERE path IN (%s)" % ",".join(
                    "?" for _ in chunk
                )
                for row in cur.execute(q, chunk):
                    out[row["path"]] = CachedFile(**{k: row[k] for k in row.keys()})
        except sqlite3.Error as exc:
            log.warning("Lectura de caché fallida: %s", exc)
        return out

    def upsert_many(self, entries: Sequence[CachedFile]) -> None:
        if self._conn is None or not entries:
            return
        try:
            self._conn.executemany(_INSERT_SQL, [e.as_row() for e in entries])
            self._conn.commit()
        except sqlite3.Error as exc:
            log.warning("Escritura de caché fallida: %s", exc)

    def prune_missing(self, existing_paths: Iterable[str]) -> int:
        """Drop rows for files that no longer exist. Returns rows removed."""
        if self._conn is None:
            return 0
        existing = set(existing_paths)
        try:
            all_paths = [r[0] for r in self._conn.execute("SELECT path FROM file_cache")]
            gone = [p for p in all_paths if p not in existing]
            for start in range(0, len(gone), 400):
                chunk = gone[start:start + 400]
                self._conn.execute(
                    "DELETE FROM file_cache WHERE path IN (%s)"
                    % ",".join("?" for _ in chunk),
                    chunk,
                )
            self._conn.commit()
            return len(gone)
        except sqlite3.Error as exc:
            log.warning("Purga de caché fallida: %s", exc)
            return 0

    def count(self) -> int:
        if self._conn is None:
            return 0
        try:
            return int(self._conn.execute("SELECT COUNT(*) FROM file_cache").fetchone()[0])
        except sqlite3.Error:
            return 0

    def clear(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute("DELETE FROM file_cache")
            self._conn.commit()
        except sqlite3.Error as exc:
            log.warning("No se pudo limpiar la caché: %s", exc)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None
