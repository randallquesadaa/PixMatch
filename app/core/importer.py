"""Import & organise: move photos / videos from a source folder (a phone, an SD
card) into a date/country-ordered library, skipping what is already there.

Layout produced::

    <library>/<country>/<YYYY>/<YYYY-MM>/<YYYYMMDD_HHMMSS.ext>

``<country>`` comes from the file's own GPS metadata (offline, via
:mod:`app.core.geo`) or the configured "no location" label. ``<name>`` is the
capture date, built with :func:`app.core.renamer.format_name`.

Safety (same principles as the delete / rename tools):
  * a move is *copy -> verify SHA-256 at the destination -> remove the source*;
    if any step fails the source is left untouched
  * an existing library file is never overwritten (a numeric suffix is added)
  * a file already in the library (same bytes, or pixel-identical) is reported,
    never moved -- its source is removed only if the user ticks that row
  * every action is written to the operation history and can be undone
"""

from __future__ import annotations

import os
import time
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from app.core.file_ops import prune_empty_parents, safe_size, verified_copy_move
from app.core.geo import CountryResolver, gps_from_exif, parse_iso6709
from app.core.hashing import HashCancelled, hash_file
from app.core.image_analyzer import pixel_digest as compute_pixel_digest
from app.core.renamer import (
    DEFAULT_PATTERN,
    DateSource,
    capture_datetime,
    format_name,
    parse_datetime_string,
)
from app.core.scanner import FileKind
from app.database.database import FileCacheDB
from app.database.models import CachedFile
from app.utils.file_utils import extended_path
from app.utils.logging_setup import get_logger

log = get_logger(__name__)

_JPEG_EXTS = {".jpeg", ".jpe", ".jfif"}
_INVALID_SEGMENT_CHARS = '<>:"|?*\\/\x00'


class ImportStatus(str, Enum):
    NEW = "new"
    NEW_SUFFIXED = "new_suffixed"  # will import, but the target name got a suffix
    DUPLICATE_EXACT = "duplicate_exact"  # byte-identical file already in the library
    DUPLICATE_PIXEL = "duplicate_pixel"  # different bytes, identical pixels
    DUPLICATE_IN_BATCH = "duplicate_in_batch"  # identical to an earlier source file
    ERROR = "error"

    @property
    def label(self) -> str:
        return {
            ImportStatus.NEW: "nuevo",
            ImportStatus.NEW_SUFFIXED: "nuevo (nombre con sufijo)",
            ImportStatus.DUPLICATE_EXACT: "ya existe (idéntica)",
            ImportStatus.DUPLICATE_PIXEL: "ya existe (píxeles idénticos)",
            ImportStatus.DUPLICATE_IN_BATCH: "duplicada en este lote",
            ImportStatus.ERROR: "error",
        }[self]

    @property
    def is_new(self) -> bool:
        return self in (ImportStatus.NEW, ImportStatus.NEW_SUFFIXED)

    @property
    def is_duplicate(self) -> bool:
        return self in (
            ImportStatus.DUPLICATE_EXACT,
            ImportStatus.DUPLICATE_PIXEL,
            ImportStatus.DUPLICATE_IN_BATCH,
        )


class LocationSource(str, Enum):
    EXIF_GPS = "exif_gps"
    VIDEO_GPS = "video_gps"
    NONE = "none"

    @property
    def label(self) -> str:
        return {
            LocationSource.EXIF_GPS: "GPS de la foto",
            LocationSource.VIDEO_GPS: "GPS del vídeo",
            LocationSource.NONE: "sin ubicación en los metadatos",
        }[self]


@dataclass
class ImportPlan:
    source_path: str
    kind: str  # "image" | "video"
    size: int
    timestamp: datetime | None
    date_source: DateSource
    country: str  # folder name (already resolved / sanitised)
    location_source: LocationSource
    dest_dir: str
    dest_name: str
    status: ImportStatus
    duplicate_of: str | None = None
    error: str | None = None
    sha256: str | None = None
    enabled: bool = True
    delete_source_if_dup: bool = False

    @property
    def dest_path(self) -> str:
        return os.path.join(self.dest_dir, self.dest_name)

    @property
    def will_import(self) -> bool:
        return self.enabled and self.status.is_new

    @property
    def will_delete_source(self) -> bool:
        return self.enabled and self.status.is_duplicate and self.delete_source_if_dup


@dataclass
class ImportOutcome:
    source_path: str
    dest_path: str
    action: str  # "moved" | "deleted_source"
    ok: bool
    size: int = 0
    error: str | None = None


@dataclass
class ImportReport:
    outcomes: list[ImportOutcome]
    created_dirs: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def moved(self) -> list[ImportOutcome]:
        return [o for o in self.outcomes if o.ok and o.action == "moved"]

    @property
    def source_deleted(self) -> list[ImportOutcome]:
        return [o for o in self.outcomes if o.ok and o.action == "deleted_source"]

    @property
    def failed(self) -> list[ImportOutcome]:
        return [o for o in self.outcomes if not o.ok]

    @property
    def moved_bytes(self) -> int:
        return sum(o.size for o in self.moved)


# ---------------------------------------------------------------------------
# Shared with app.core.organizer -- placing a file in a country/date library
# is the same problem whether the source is external (import) or the library
# reorganising itself in place (organize).
def fold_name(name: str) -> str:
    """Casefold + strip diacritics, for tolerant matching of folder and file
    names ('México' / 'Mexico', 'Sin ubicación' / 'Sin ubicacion') so placing
    a file into a library that already exists never spawns a near-duplicate
    folder."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def sanitize_segment(name: str, fallback: str = "Sin ubicación") -> str:
    cleaned = "".join(c for c in name if c not in _INVALID_SEGMENT_CHARS).strip().strip(".")
    return cleaned or fallback


def is_within(path: str, root: str) -> bool:
    try:
        common = os.path.commonpath([os.path.realpath(path), os.path.realpath(root)])
    except ValueError:  # different drives on Windows
        return False
    return common == os.path.realpath(root)


# ---------------------------------------------------------------------------
class LibraryIndex:
    """Answers "is this file already in the library?".

    * exact match: SHA-256, with a byte-size prefilter (only library files that
      share a size with an incoming file are ever hashed);
    * pixel match: decoded-pixel digest, with a dimension prefilter built by
      :meth:`prepare_dims` (reads image headers, not full decodes).

    The shared :class:`FileCacheDB` is consulted first, so a library already
    analysed by the Duplicados tab is neither re-hashed nor re-decoded.
    """

    def __init__(self, root: str, db: FileCacheDB | None = None) -> None:
        self.root = root
        self._db = db
        self.by_size: dict[int, list] = {}
        self._images: list = []
        self._by_dims: dict[tuple[int, int], list] = {}
        self._cache: dict[str, CachedFile] = {}
        self._sha: dict[str, str | None] = {}
        self._pixel: dict[str, str | None] = {}
        self._new_rows: dict[str, CachedFile] = {}

    def build(self, scanned: Iterable) -> None:
        scanned = list(scanned)
        for sf in scanned:
            self.by_size.setdefault(sf.size, []).append(sf)
            if sf.kind is FileKind.IMAGE:
                self._images.append(sf)
        if self._db is not None and self._db.available:
            self._cache = self._db.get_many(sf.path for sf in scanned)

    def prepare_dims(
        self,
        *,
        on_progress: Callable[[int, int], None] | None = None,
        check: Callable[[], bool] | None = None,
    ) -> None:
        """Index every library image by (width, height). Uses the cache where
        possible and otherwise reads just the image header."""
        total = len(self._images)
        for i, sf in enumerate(self._images):
            if check is not None and not check():
                break
            dims = self._dims_for(sf)
            if dims:
                self._by_dims.setdefault(dims, []).append(sf)
            if on_progress is not None and (i % 200 == 0 or i == total - 1):
                on_progress(i + 1, total)

    def _dims_for(self, sf) -> tuple[int, int] | None:
        row = self._row(sf)
        if row and row.width and row.height:
            return (row.width, row.height)
        from app.core.metadata import read_image_info

        info = read_image_info(sf.path)
        if info.error or not info.width:
            return None
        dims = info.oriented_size
        self._stash(sf, width=dims[0], height=dims[1])
        return dims

    def _row(self, sf):
        row = self._cache.get(sf.path)
        return row if row and row.is_valid_for(sf.size, sf.mtime) else None

    def _stash(self, sf, **fields) -> None:
        row = self._new_rows.get(sf.path)
        if row is None:
            row = CachedFile(path=sf.path, size=sf.size, mtime=sf.mtime, analyzed_at=time.time())
        for key, value in fields.items():
            setattr(row, key, value)
        self._new_rows[sf.path] = row

    def sha_for(self, sf, *, check: Callable[[], bool] | None = None) -> str | None:
        if sf.path in self._sha:
            return self._sha[sf.path]
        row = self._row(sf)
        if row and row.sha256:
            self._sha[sf.path] = row.sha256
            return row.sha256
        try:
            digest = hash_file(sf.path, check=check)
        except (OSError, HashCancelled):
            digest = None
        self._sha[sf.path] = digest
        if digest is not None:
            self._stash(sf, sha256=digest)
        return digest

    def pixel_for(self, sf) -> str | None:
        if sf.path in self._pixel:
            return self._pixel[sf.path]
        row = self._row(sf)
        if row and row.pixel_digest:
            self._pixel[sf.path] = row.pixel_digest
            return row.pixel_digest
        result = compute_pixel_digest(sf.path)
        if result is None:
            self._pixel[sf.path] = None
            return None
        digest, info = result
        self._pixel[sf.path] = digest
        self._stash(sf, pixel_digest=digest, width=info.width, height=info.height)
        return digest

    def find_exact(self, sha: str, size: int, *, check=None) -> str | None:
        for sf in self.by_size.get(size, []):
            if self.sha_for(sf, check=check) == sha:
                return sf.path
        return None

    def find_pixel(self, src_digest: str, src_dims: tuple[int, int]) -> str | None:
        for sf in self._by_dims.get(src_dims, []):
            if self.pixel_for(sf) == src_digest:
                return sf.path
        return None

    def flush(self) -> None:
        if self._db is not None and self._db.available and self._new_rows:
            try:
                self._db.upsert_many(list(self._new_rows.values()))
            except Exception as exc:  # cache is best-effort
                log.warning("No se pudo actualizar la caché de la biblioteca: %s", exc)


# ---------------------------------------------------------------------------
def existing_country_dirs(root: str) -> dict[str, str]:
    try:
        return {
            fold_name(e.name): e.name for e in os.scandir(root) if e.is_dir(follow_symlinks=False)
        }
    except OSError:
        return {}


def capture_moment(sf, kind: str, video_info) -> tuple[datetime, DateSource]:
    if kind == "video":
        if video_info is not None and getattr(video_info, "creation_time", None):
            dt = parse_datetime_string(video_info.creation_time)
            if dt is not None:
                return dt, DateSource.VIDEO_CREATION
        return datetime.fromtimestamp(sf.mtime), DateSource.FILE_MTIME
    return capture_datetime(sf.path, "image", sf.mtime)


def resolve_location(path: str, kind: str, video_info, resolver: CountryResolver):
    if kind == "image":
        coord = gps_from_exif(path)
        src = LocationSource.EXIF_GPS
    else:
        raw = getattr(video_info, "location", None) if video_info is not None else None
        coord = parse_iso6709(raw)
        src = LocationSource.VIDEO_GPS
    if coord is None:
        return None, LocationSource.NONE
    country = resolver.country_for(coord)
    if country is None:
        return None, LocationSource.NONE
    return country, src


def resolve_country_folder(country, aliases, existing_dirs, no_location) -> str:
    name = no_location if not country else aliases.get(fold_name(country), country)
    name = sanitize_segment(name, no_location)
    return existing_dirs.get(fold_name(name), name)


def assign_unique_dest_names(plans: list, *, pending_status, suffixed_status) -> None:
    """Give every *plan* whose ``status`` is exactly *pending_status* a name
    that is unique in its ``dest_dir``, following the convention already used
    by real libraries: a lone file keeps its plain date name, but as soon as
    two or more would land on the same name they *all* get a zero-padded
    suffix (``_01``, ``_02``...) -- a bare name is never left sitting next to
    a suffixed one. Mutates ``dest_name`` (and ``status``, to *suffixed_status*
    where a suffix was needed) in place. Shared by the import and organise
    tools -- each just passes its own status enum values.

    A pending file already sitting in its own ``dest_dir`` (the organise tool
    renaming something in place, e.g. a burst that is already correctly
    suffixed) does not block its own slot -- see below. The one case still
    not accounted for: a file about to *leave* ``dest_dir`` for some *other*
    folder, while a different file from elsewhere is arriving into
    ``dest_dir`` with a colliding name -- that arriving file may get an
    unnecessary suffix (never an unsafe one; the departing file's name is a
    different plan's problem, not this one's).
    """
    by_dir: dict[str, list] = {}
    for p in plans:
        if p.status == pending_status:
            by_dir.setdefault(p.dest_dir, []).append(p)

    for dest_dir, dir_plans in by_dir.items():
        try:
            on_disk = {fold_name(n) for n in os.listdir(extended_path(dest_dir))}
        except OSError:
            on_disk = set()

        # A pending file that is already sitting in dest_dir (the organise
        # tool renaming something in place) will vacate its current name as
        # part of this very batch -- don't let that name block anything, or
        # re-running the same reorganisation would keep reshuffling suffixes
        # instead of settling (_01/_02 -> _03/_04 -> _01/_02 -> ...).
        dest_dir_norm = os.path.normcase(os.path.abspath(dest_dir))
        for p in dir_plans:
            src_dir_norm = os.path.normcase(os.path.abspath(os.path.dirname(p.source_path)))
            if src_dir_norm == dest_dir_norm:
                on_disk.discard(fold_name(os.path.basename(p.source_path)))

        groups: dict[str, list] = {}
        for p in dir_plans:
            groups.setdefault(fold_name(p.dest_name), []).append(p)

        for group in groups.values():
            base_name = group[0].dest_name
            if len(group) == 1 and fold_name(base_name) not in on_disk:
                on_disk.add(fold_name(base_name))
                continue  # the only one -> stays bare

            ordered = sorted(group, key=lambda p: (p.timestamp or datetime.min, p.source_path))
            stem, ext = os.path.splitext(base_name)
            n = 1
            for p in ordered:
                candidate = f"{stem}_{n:02d}{ext}"
                while fold_name(candidate) in on_disk:
                    n += 1
                    candidate = f"{stem}_{n:02d}{ext}"
                p.dest_name = candidate
                p.status = suffixed_status
                on_disk.add(fold_name(candidate))
                n += 1


def build_import_plan(
    source_files: Iterable,
    *,
    library_root: str,
    index: LibraryIndex,
    resolver: CountryResolver,
    config,
    video_infos: dict | None = None,
    check: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[ImportPlan]:
    source_files = list(source_files)
    video_infos = video_infos or {}
    pattern = config.import_pattern or DEFAULT_PATTERN
    no_location = (config.import_no_location_label or "Sin ubicación").strip() or "Sin ubicación"
    lowercase = bool(config.import_lowercase_ext)
    normalise_jpeg = bool(config.import_normalise_jpeg)
    match_pixel = bool(config.import_match_pixel_identical)
    aliases = {
        fold_name(str(k).strip()): str(v).strip()
        for k, v in (config.import_country_aliases or {}).items()
        if str(v).strip()
    }
    existing_dirs = existing_country_dirs(library_root)

    plans: list[ImportPlan] = []
    seen_sha: dict[str, str] = {}
    seen_pixel: dict[str, str] = {}
    total = len(source_files)

    for i, sf in enumerate(source_files):
        if check is not None and not check():
            break
        plans.append(
            _plan_one(
                sf,
                library_root=library_root,
                index=index,
                resolver=resolver,
                video_info=video_infos.get(sf.path),
                pattern=pattern,
                no_location=no_location,
                lowercase=lowercase,
                normalise_jpeg=normalise_jpeg,
                match_pixel=match_pixel,
                aliases=aliases,
                existing_dirs=existing_dirs,
                seen_sha=seen_sha,
                seen_pixel=seen_pixel,
                check=check,
            )
        )
        if on_progress is not None and (i % 20 == 0 or i == total - 1):
            on_progress(i + 1, total)

    assign_unique_dest_names(
        plans, pending_status=ImportStatus.NEW, suffixed_status=ImportStatus.NEW_SUFFIXED
    )
    return plans


def _plan_one(
    sf,
    *,
    library_root,
    index,
    resolver,
    video_info,
    pattern,
    no_location,
    lowercase,
    normalise_jpeg,
    match_pixel,
    aliases,
    existing_dirs,
    seen_sha,
    seen_pixel,
    check,
) -> ImportPlan:
    kind = sf.kind.value if hasattr(sf.kind, "value") else str(sf.kind)

    def err(message: str) -> ImportPlan:
        return ImportPlan(
            source_path=sf.path,
            kind=kind,
            size=sf.size,
            timestamp=None,
            date_source=DateSource.FILE_MTIME,
            country=no_location,
            location_source=LocationSource.NONE,
            dest_dir="",
            dest_name=os.path.basename(sf.path),
            status=ImportStatus.ERROR,
            error=message,
        )

    try:
        dt, date_source = capture_moment(sf, kind, video_info)
    except Exception as exc:  # never let one bad file abort the batch
        return err(f"No se pudo leer la fecha: {exc}")

    country, loc_source = resolve_location(sf.path, kind, video_info, resolver)
    country_folder = resolve_country_folder(country, aliases, existing_dirs, no_location)

    stem_ext = os.path.splitext(sf.path)[1]
    ext = stem_ext.lower() if lowercase else stem_ext
    if normalise_jpeg and ext.lower() in _JPEG_EXTS:
        ext = ".jpg" if lowercase else ".JPG"
    base_name = format_name(dt, pattern, "", ext)

    dest_dir = os.path.join(library_root, country_folder, f"{dt:%Y}", f"{dt:%Y-%m}")

    # --- duplicate detection -------------------------------------------
    try:
        src_sha = hash_file(sf.path, check=check)
    except HashCancelled:
        raise
    except OSError as exc:
        return err(f"No se pudo leer el archivo: {exc}")

    status = ImportStatus.NEW
    duplicate_of: str | None = None

    if src_sha in seen_sha:
        status, duplicate_of = ImportStatus.DUPLICATE_IN_BATCH, seen_sha[src_sha]
    else:
        lib_hit = index.find_exact(src_sha, sf.size, check=check)
        if lib_hit:
            status, duplicate_of = ImportStatus.DUPLICATE_EXACT, lib_hit

    if status is ImportStatus.NEW and kind == "image" and match_pixel:
        src_pix = compute_pixel_digest(sf.path)
        if src_pix is not None:
            digest, info = src_pix
            dims = (info.width, info.height)
            if digest in seen_pixel:
                status, duplicate_of = ImportStatus.DUPLICATE_IN_BATCH, seen_pixel[digest]
            else:
                pix_hit = index.find_pixel(digest, dims)
                if pix_hit:
                    status, duplicate_of = ImportStatus.DUPLICATE_PIXEL, pix_hit
            seen_pixel.setdefault(digest, sf.path)

    seen_sha.setdefault(src_sha, sf.path)

    # Final naming (bare vs. "_01"/"_02"...) needs to see every NEW file bound
    # for the same folder at once, so it is resolved afterwards by
    # assign_unique_dest_names(); here dest_name is just the plain date name.
    return ImportPlan(
        source_path=sf.path,
        kind=kind,
        size=sf.size,
        timestamp=dt,
        date_source=date_source,
        country=country_folder,
        location_source=loc_source,
        dest_dir=dest_dir,
        dest_name=base_name,
        status=status,
        duplicate_of=duplicate_of,
        sha256=src_sha,
    )


def apply_import(
    plans: Iterable[ImportPlan],
    *,
    history=None,
    progress: Callable[[int, int, str], None] | None = None,
    check: Callable[[], bool] | None = None,
) -> ImportReport:
    plans = list(plans)
    todo = [p for p in plans if p.will_import or p.will_delete_source]
    outcomes: list[ImportOutcome] = []
    created_dirs: list[str] = []
    cancelled = False
    total = len(todo)

    for idx, p in enumerate(todo, start=1):
        if check is not None and not check():
            cancelled = True
            break

        if p.will_import:
            try:
                error = verified_copy_move(
                    p.source_path,
                    p.dest_path,
                    p.dest_dir,
                    p.size,
                    p.sha256,
                    created_dirs,
                    check,
                    tmp_prefix="pixmatch-import",
                )
            except HashCancelled:
                cancelled = True
                break
            ok = error is None or error.startswith("Copiado,")
            outcomes.append(
                ImportOutcome(p.source_path, p.dest_path, "moved", ok, p.size if ok else 0, error)
            )
        else:
            error = _trash(p.source_path)
            outcomes.append(
                ImportOutcome(p.source_path, p.dest_path, "deleted_source", error is None, 0, error)
            )

        if progress is not None:
            progress(idx, total, os.path.basename(p.source_path))

    _record_history(history, outcomes, plans)
    return ImportReport(outcomes=outcomes, created_dirs=created_dirs, cancelled=cancelled)


def undo_import(
    outcomes: Iterable[ImportOutcome],
    *,
    history=None,
    check: Callable[[], bool] | None = None,
) -> ImportReport:
    """Move every successfully-moved file back to its original source path."""
    results: list[ImportOutcome] = []
    for o in outcomes:
        if not o.ok or o.action != "moved":
            continue
        if check is not None and not check():
            break
        src_dir = os.path.dirname(o.source_path)
        try:
            error = verified_copy_move(
                o.dest_path,
                o.source_path,
                src_dir,
                safe_size(o.dest_path),
                None,
                [],
                check,
                tmp_prefix="pixmatch-import",
            )
        except HashCancelled:
            break
        if error is None:
            prune_empty_parents(os.path.dirname(o.dest_path))
        results.append(
            ImportOutcome(o.dest_path, o.source_path, "moved", error is None, o.size, error)
        )
    _record_history(history, results, [])
    return ImportReport(outcomes=results)


def _trash(path: str) -> str | None:
    try:
        from send2trash import send2trash

        send2trash(os.fspath(path))
        return None
    except Exception as exc:
        return f"No se pudo enviar a la papelera: {exc}"


def _record_history(history, outcomes: list[ImportOutcome], plans) -> None:
    if history is None or not outcomes:
        return
    try:
        from app.database.history import OperationEntry

        by_src = {p.source_path: p for p in plans} if plans else {}
        for o in outcomes:
            plan = by_src.get(o.source_path)
            if o.action == "deleted_source":
                action = "import-dupe"
                detail = (
                    o.error
                    if not o.ok
                    else f"ya en la biblioteca ({plan.duplicate_of if plan else '—'}); "
                    "origen enviado a la papelera"
                )
            else:
                action = "import"
                detail = o.error if o.error else f"{o.source_path} → {o.dest_path}"
            history.record(
                OperationEntry.now(
                    path=o.dest_path or o.source_path,
                    action=action,
                    result="ok" if o.ok else "error",
                    detail=detail,
                    size=o.size or (plan.size if plan else None),
                )
            )
    except Exception as exc:
        log.warning("No se pudo registrar la importación: %s", exc)
