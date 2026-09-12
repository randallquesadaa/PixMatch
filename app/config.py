"""User configuration, persisted as JSON in the platform config directory.

Only the options that are actually wired up in Phase 1 are honoured today;
the rest are stored and surfaced in the Settings dialog so later phases can
pick them up without a migration.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any

from app.utils.paths import config_dir

log = logging.getLogger(__name__)
CONFIG_PATH = config_dir() / "settings.json"

# Directory names commonly worth excluding. They are *offered*, never applied
# automatically - the user ticks the ones they want.
COMMON_EXCLUDES = [
    "node_modules",
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    ".cache",
    "cache",
    "Cache",
    "tmp",
    "temp",
    "Temp",
    "$RECYCLE.BIN",
    "System Volume Information",
    ".Trash",
    ".Trashes",
    "Thumbs.db",
]


@dataclass
class AppConfig:
    # appearance
    theme: str = "dark"  # "dark" | "light"
    language: str = "es"

    # scanning
    follow_symlinks: bool = False
    min_size_mb: float = 0.0
    ignored_extensions: list[str] = field(default_factory=list)
    excluded_dir_names: list[str] = field(default_factory=list)
    excluded_paths: list[str] = field(default_factory=list)

    # analysis
    analyze_images: bool = True
    analyze_videos: bool = False  # Phase 4 - needs FFmpeg
    ffmpeg_path: str = ""  # file or directory; empty -> auto-detect
    ffprobe_path: str = ""
    detect_pixel_identical: bool = True  # Phase 2 - decode & compare real pixels
    analyze_similar_images: bool = False  # Phase 2/6 - combined similarity engine
    similarity_threshold: int = 90  # legacy single threshold (maps to bands.similar)
    workers: int = max(2, (os.cpu_count() or 4))
    use_gpu: bool = False  # Phase 5

    # --- Phase 6: intelligent similarity ---
    sensitivity: str = "medium"  # "low" | "medium" | "high" | "custom"
    similarity_bands: dict = field(
        default_factory=lambda: {
            "visually_identical": 98.0,
            "very_similar": 90.0,
            "similar": 78.0,
        }
    )
    similarity_weights: dict = field(
        default_factory=lambda: {
            "phash": 0.40,
            "dhash": 0.20,
            "ahash": 0.10,
            "color": 0.10,
            "bhash": 0.20,
        }
    )
    detect_resized: bool = True  # label same-image-different-resolution
    detect_crops: bool = False  # detect one image being a crop of another
    dhash_confirm_slack: int = 12  # (custom mode) dHash confirmation tolerance
    use_ai_embeddings: bool = False  # optional CLIP-style visual embeddings

    # deletion (Phase 3)
    deletion_mode: str = "trash"  # "trash" | "permanent"
    confirm_deletions: bool = True  # always ask before deleting

    # rename-by-date tool
    rename_pattern: str = "%Y%m%d_%H%M%S"
    rename_prefix: str = ""  # e.g. "IMG_"
    rename_include_videos: bool = True
    rename_lowercase_ext: bool = True
    rename_normalise_jpeg: bool = True  # .jpeg/.jpe -> .jpg

    # import / organise tool (move from a phone/card into an ordered library)
    import_library_folder: str = ""  # destination photo library root
    import_source_folder: str = ""  # last source folder (session memory)
    import_pattern: str = "%Y%m%d_%H%M%S"
    import_include_videos: bool = True
    import_lowercase_ext: bool = True
    import_normalise_jpeg: bool = True
    import_no_location_label: str = "Sin ubicación"
    import_country_aliases: dict = field(default_factory=dict)
    import_match_pixel_identical: bool = True

    # organize tool (rearrange an already-existing library in place; shares
    # the naming settings above -- it is the same convention, just applied
    # without a separate source/destination)
    organize_root_folder: str = ""  # last folder organised (session memory)

    # storage
    use_cache: bool = True  # SQLite analysis cache
    cache_db_path: str = ""  # empty -> default location

    # session memory (not really settings, but handy to persist)
    last_folder: str = ""

    # ---------------------------------------------------------------
    @property
    def min_size_bytes(self) -> int:
        return int(max(0.0, self.min_size_mb) * 1024 * 1024)

    def effective_db_path(self) -> str:
        if self.cache_db_path.strip():
            return self.cache_db_path.strip()
        from app.utils.paths import data_dir

        return str(data_dir() / "analysis-cache.sqlite3")

    def save(self) -> None:
        try:
            CONFIG_PATH.write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover
            log.warning("No se pudo guardar la configuración: %s", exc)

    @classmethod
    def load(cls) -> AppConfig:
        if not CONFIG_PATH.exists():
            return cls()
        try:
            raw: dict[str, Any] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Configuración ilegible, se usan valores por defecto: %s", exc)
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})
