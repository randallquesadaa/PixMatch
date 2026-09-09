"""Recursive, read-only directory scanner.

Design notes
------------
* Iterative (explicit stack), so arbitrarily deep trees never hit the Python
  recursion limit.
* Symlinked directories are skipped by default to avoid cycles. Even when the
  user opts to follow them, every visited directory's (device, inode) pair is
  remembered so a loop can never run forever.
* A failure on one entry (permissions, vanished file, unreadable directory)
  is recorded and scanning continues.
* Unicode names and paths with spaces are handled natively via ``os.scandir``.
* The scanner only ever *reads* metadata. It never opens file contents.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from app.utils.control import RunController
from app.utils.file_utils import extended_path

# Extension tables. The architecture allows adding formats later - just extend
# these sets (and, for real decoding support, install the matching library).
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".dib",
    ".tif",
    ".tiff",
    ".heic",
    ".heif",
    ".avif",
}
VIDEO_EXTENSIONS = {
    ".mp4",
    ".m4v",
    ".mov",
    ".avi",
    ".mkv",
    ".wmv",
    ".webm",
    ".mpeg",
    ".mpg",
    ".mpe",
    ".3gp",
    ".3g2",
    ".m2ts",
    ".mts",
    ".ts",
    ".flv",
}


class FileKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    OTHER = "other"


def classify_extension(name: str) -> FileKind:
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return FileKind.IMAGE
    if ext in VIDEO_EXTENSIONS:
        return FileKind.VIDEO
    return FileKind.OTHER


@dataclass(slots=True)
class ScannedFile:
    path: str
    size: int
    mtime: float
    kind: FileKind


@dataclass(slots=True)
class ScanIssue:
    path: str
    message: str


@dataclass
class ScanStats:
    files_found: int = 0
    images: int = 0
    videos: int = 0
    others: int = 0
    total_size: int = 0
    folders_scanned: int = 0
    excluded_folders: int = 0
    issues: list[ScanIssue] = field(default_factory=list)

    def snapshot(self) -> ScanStats:
        return ScanStats(
            self.files_found,
            self.images,
            self.videos,
            self.others,
            self.total_size,
            self.folders_scanned,
            self.excluded_folders,
            list(self.issues),
        )


@dataclass
class ScanOptions:
    follow_symlinks: bool = False
    min_size_bytes: int = 0
    ignored_extensions: frozenset[str] = frozenset()
    excluded_dir_names: frozenset[str] = frozenset()
    excluded_paths: frozenset[str] = frozenset()
    include_kinds: frozenset[FileKind] = frozenset({FileKind.IMAGE, FileKind.VIDEO, FileKind.OTHER})

    @staticmethod
    def normalise_ext(exts: Iterable[str]) -> frozenset[str]:
        out = set()
        for e in exts:
            e = e.strip().lower()
            if e and not e.startswith("."):
                e = "." + e
            if e:
                out.add(e)
        return frozenset(out)


OnFile = Callable[[ScannedFile], None]
OnProgress = Callable[[ScanStats], None]


class Scanner:
    def __init__(
        self,
        root: str | os.PathLike[str],
        options: ScanOptions | None = None,
        controller: RunController | None = None,
    ) -> None:
        self.root = Path(root)
        self.options = options or ScanOptions()
        self.controller = controller or RunController()
        self._excluded_real = {os.path.realpath(p) for p in self.options.excluded_paths}

    def scan(
        self,
        on_file: OnFile | None = None,
        on_progress: OnProgress | None = None,
        progress_every: int = 200,
    ) -> ScanStats:
        stats = ScanStats()
        root = self.root
        if not root.exists():
            stats.issues.append(ScanIssue(str(root), "La carpeta no existe."))
            return stats
        if not root.is_dir():
            stats.issues.append(ScanIssue(str(root), "La ruta no es una carpeta."))
            return stats

        visited: set[tuple[int, int]] = set()
        stack: list[str] = [str(root)]

        while stack:
            if not self.controller.checkpoint():
                break
            current = stack.pop()

            try:
                dir_stat = os.stat(extended_path(current))
                key = (dir_stat.st_dev, dir_stat.st_ino)
                if key in visited:
                    continue
                visited.add(key)
            except OSError as exc:
                stats.issues.append(
                    ScanIssue(current, f"No se pudo acceder: {exc.strerror or exc}")
                )
                continue

            try:
                entries = list(os.scandir(current))
            except PermissionError:
                stats.issues.append(ScanIssue(current, "Permisos insuficientes."))
                continue
            except OSError as exc:
                stats.issues.append(
                    ScanIssue(current, f"No se pudo leer la carpeta: {exc.strerror or exc}")
                )
                continue

            stats.folders_scanned += 1

            for entry in entries:
                if not self.controller.checkpoint():
                    return stats
                try:
                    self._handle_entry(entry, stack, stats, on_file)
                except OSError as exc:
                    stats.issues.append(
                        ScanIssue(entry.path, f"No se pudo procesar: {exc.strerror or exc}")
                    )

            if on_progress is not None:
                on_progress(stats.snapshot())

        if on_progress:
            on_progress(stats.snapshot())
        return stats

    # ------------------------------------------------------------------
    def _handle_entry(
        self,
        entry: os.DirEntry,
        stack: list[str],
        stats: ScanStats,
        on_file: OnFile | None,
    ) -> None:
        opts = self.options

        is_symlink = entry.is_symlink()

        try:
            is_dir = entry.is_dir(follow_symlinks=opts.follow_symlinks)
        except OSError:
            is_dir = False

        if is_dir:
            if is_symlink and not opts.follow_symlinks:
                return  # skip symlinked dirs -> no cycles
            if entry.name in opts.excluded_dir_names:
                stats.excluded_folders += 1
                return
            if os.path.realpath(entry.path) in self._excluded_real:
                stats.excluded_folders += 1
                return
            stack.append(entry.path)
            return

        # regular file (or symlink to a file)
        try:
            if not entry.is_file(follow_symlinks=opts.follow_symlinks):
                return
        except OSError:
            return

        if is_symlink and not opts.follow_symlinks:
            return

        ext = os.path.splitext(entry.name)[1].lower()
        if ext in opts.ignored_extensions:
            return

        kind = classify_extension(entry.name)
        if kind not in opts.include_kinds:
            # still count it so the statistics are complete
            pass

        try:
            st = entry.stat(follow_symlinks=True)
        except OSError as exc:
            stats.issues.append(ScanIssue(entry.path, f"No se pudo leer: {exc.strerror or exc}"))
            return

        size = st.st_size
        if size < opts.min_size_bytes:
            return

        stats.files_found += 1
        stats.total_size += size
        if kind is FileKind.IMAGE:
            stats.images += 1
        elif kind is FileKind.VIDEO:
            stats.videos += 1
        else:
            stats.others += 1

        if on_file is not None and kind in opts.include_kinds:
            on_file(ScannedFile(path=entry.path, size=size, mtime=st.st_mtime, kind=kind))
