"""Row types for the analysis cache."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

SCHEMA_VERSION = 3

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_cache (
    path          TEXT PRIMARY KEY,
    size          INTEGER NOT NULL,
    mtime         REAL    NOT NULL,
    sha256        TEXT,
    pixel_digest  TEXT,
    phash         TEXT,
    dhash         TEXT,
    ahash         TEXT,
    bhash         TEXT,
    color_sig     TEXT,
    embedding     TEXT,
    width         INTEGER,
    height        INTEGER,
    img_format    TEXT,
    color_mode    TEXT,
    orientation   INTEGER,
    has_exif      INTEGER,
    camera_make   TEXT,
    camera_model  TEXT,
    date_taken    TEXT,
    decode_error  TEXT,
    video_duration REAL,
    video_width    INTEGER,
    video_height   INTEGER,
    video_codec    TEXT,
    video_fps      REAL,
    video_bitrate  INTEGER,
    video_audio    TEXT,
    frame_hashes   TEXT,
    analyzed_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_file_cache_sha256 ON file_cache(sha256);
CREATE INDEX IF NOT EXISTS ix_file_cache_pixel  ON file_cache(pixel_digest);
"""

_FIELDS = (
    "path", "size", "mtime", "sha256", "pixel_digest",
    "phash", "dhash", "ahash", "bhash", "color_sig", "embedding",
    "width", "height", "img_format", "color_mode",
    "orientation", "has_exif", "camera_make", "camera_model", "date_taken",
    "decode_error",
    "video_duration", "video_width", "video_height", "video_codec",
    "video_fps", "video_bitrate", "video_audio", "frame_hashes",
    "analyzed_at",
)


@dataclass(slots=True)
class CachedFile:
    path: str
    size: int
    mtime: float
    sha256: Optional[str] = None
    pixel_digest: Optional[str] = None
    phash: Optional[str] = None
    dhash: Optional[str] = None
    ahash: Optional[str] = None
    bhash: Optional[str] = None
    color_sig: Optional[str] = None
    embedding: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    img_format: Optional[str] = None
    color_mode: Optional[str] = None
    orientation: Optional[int] = None
    has_exif: Optional[int] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    date_taken: Optional[str] = None
    decode_error: Optional[str] = None
    video_duration: Optional[float] = None
    video_width: Optional[int] = None
    video_height: Optional[int] = None
    video_codec: Optional[str] = None
    video_fps: Optional[float] = None
    video_bitrate: Optional[int] = None
    video_audio: Optional[str] = None
    frame_hashes: Optional[str] = None
    analyzed_at: float = 0.0

    def is_valid_for(self, size: int, mtime: float, *, mtime_tol: float = 1e-3) -> bool:
        """The cache row can be trusted only if size and mtime still match."""
        return self.size == size and abs(self.mtime - mtime) <= mtime_tol

    def as_row(self) -> tuple:
        return tuple(getattr(self, f) for f in _FIELDS)

    @classmethod
    def fields(cls) -> tuple[str, ...]:
        return _FIELDS
