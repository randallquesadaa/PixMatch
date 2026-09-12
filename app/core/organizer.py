"""Organise a library in place: take an already-existing, disorganised folder
(an old hard drive, a phone dump copied over the years with no order) and
rearrange it into the same layout the import tool produces::

    <root>/<country>/<YYYY>/<YYYY-MM>/<YYYYMMDD_HHMMSS.ext>

This is deliberately the *simple* tool: no comparison against anything, no
"already exists" evaluation, no deletion -- a file is either moved to where
it belongs, or (if it is already there) left alone. Two files that end up
wanting the same name still get separated with the usual "_01"/"_02" suffix
(:func:`app.core.importer.assign_unique_dest_names`), but that is a *naming*
collision, not a judgement about which one to keep.

Safety:
  * same filesystem (the overwhelmingly common case, since source and
    destination are the same drive) -> a plain atomic rename; a rename can
    never corrupt bytes, so no extra verification is needed
  * a different filesystem (e.g. a mounted sub-volume inside the root) falls
    back to the import tool's copy -> verify SHA-256 -> remove
  * a file already at its correct place is left untouched (status
    UNCHANGED) -- running this twice in a row is a no-op
  * an existing file at the destination is never overwritten (a numeric
    suffix is added instead)
  * every move is written to the operation history and can be undone
"""

from __future__ import annotations

import errno
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from app.core.file_ops import (
    makedirs_tracked,
    os_error_message,
    prune_empty_parents,
    safe_size,
    verified_copy_move,
)
from app.core.geo import CountryResolver
from app.core.hashing import HashCancelled, hash_file
from app.core.importer import (
    DEFAULT_PATTERN,
    LocationSource,
    assign_unique_dest_names,
    capture_moment,
    existing_country_dirs,
    fold_name,
    resolve_country_folder,
    resolve_location,
)
from app.core.renamer import DateSource, format_name
from app.utils.file_utils import extended_path
from app.utils.logging_setup import get_logger

log = get_logger(__name__)

_JPEG_EXTS = {".jpeg", ".jpe", ".jfif"}


class OrganizeStatus(str, Enum):
    MOVE = "move"
    MOVE_SUFFIXED = "move_suffixed"  # will move, but the target name got a suffix
    UNCHANGED = "unchanged"  # already exactly where it belongs
    ERROR = "error"

    @property
    def label(self) -> str:
        return {
            OrganizeStatus.MOVE: "se moverá",
            OrganizeStatus.MOVE_SUFFIXED: "se moverá (nombre con sufijo)",
            OrganizeStatus.UNCHANGED: "ya está en su sitio",
            OrganizeStatus.ERROR: "error",
        }[self]

    @property
    def will_move_by_default(self) -> bool:
        return self in (OrganizeStatus.MOVE, OrganizeStatus.MOVE_SUFFIXED)


@dataclass
class OrganizePlan:
    source_path: str
    kind: str  # "image" | "video"
    size: int
    timestamp: datetime | None
    date_source: DateSource
    country: str  # folder name (already resolved / sanitised)
    location_source: LocationSource
    dest_dir: str
    dest_name: str
    status: OrganizeStatus
    error: str | None = None
    enabled: bool = True

    @property
    def dest_path(self) -> str:
        return os.path.join(self.dest_dir, self.dest_name)

    @property
    def will_move(self) -> bool:
        return self.enabled and self.status.will_move_by_default


@dataclass
class OrganizeOutcome:
    source_path: str
    dest_path: str
    ok: bool
    size: int = 0
    error: str | None = None


@dataclass
class OrganizeReport:
    outcomes: list[OrganizeOutcome]
    created_dirs: list[str] = field(default_factory=list)
    cancelled: bool = False

    @property
    def moved(self) -> list[OrganizeOutcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def failed(self) -> list[OrganizeOutcome]:
        return [o for o in self.outcomes if not o.ok]

    @property
    def moved_bytes(self) -> int:
        return sum(o.size for o in self.moved)


# ---------------------------------------------------------------------------
def build_organize_plan(
    source_files: Iterable,
    *,
    root: str,
    resolver: CountryResolver,
    config,
    video_infos: dict | None = None,
    check: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[OrganizePlan]:
    source_files = list(source_files)
    video_infos = video_infos or {}
    pattern = config.import_pattern or DEFAULT_PATTERN
    no_location = (config.import_no_location_label or "Sin ubicación").strip() or "Sin ubicación"
    lowercase = bool(config.import_lowercase_ext)
    normalise_jpeg = bool(config.import_normalise_jpeg)
    aliases = {
        fold_name(str(k).strip()): str(v).strip()
        for k, v in (config.import_country_aliases or {}).items()
        if str(v).strip()
    }
    existing_dirs = existing_country_dirs(root)

    plans: list[OrganizePlan] = []
    total = len(source_files)

    for i, sf in enumerate(source_files):
        if check is not None and not check():
            break
        plans.append(
            _plan_one(
                sf,
                root=root,
                resolver=resolver,
                video_info=video_infos.get(sf.path),
                pattern=pattern,
                no_location=no_location,
                lowercase=lowercase,
                normalise_jpeg=normalise_jpeg,
                aliases=aliases,
                existing_dirs=existing_dirs,
            )
        )
        if on_progress is not None and (i % 20 == 0 or i == total - 1):
            on_progress(i + 1, total)

    assign_unique_dest_names(
        plans, pending_status=OrganizeStatus.MOVE, suffixed_status=OrganizeStatus.MOVE_SUFFIXED
    )

    # A file whose batch-resolved name (after suffixing) turns out to be
    # exactly the name it already has -- common for a burst that is already
    # correctly suffixed, just renumbered by assign_unique_dest_names() while
    # its own current slot was still "taken" -- needs no move at all.
    for p in plans:
        if p.status.will_move_by_default and _same_path(p.source_path, p.dest_path):
            p.status = OrganizeStatus.UNCHANGED

    return plans


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _plan_one(
    sf,
    *,
    root,
    resolver,
    video_info,
    pattern,
    no_location,
    lowercase,
    normalise_jpeg,
    aliases,
    existing_dirs,
) -> OrganizePlan:
    kind = sf.kind.value if hasattr(sf.kind, "value") else str(sf.kind)

    def err(message: str) -> OrganizePlan:
        return OrganizePlan(
            source_path=sf.path,
            kind=kind,
            size=sf.size,
            timestamp=None,
            date_source=DateSource.FILE_MTIME,
            country=no_location,
            location_source=LocationSource.NONE,
            dest_dir="",
            dest_name=os.path.basename(sf.path),
            status=OrganizeStatus.ERROR,
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

    dest_dir = os.path.join(root, country_folder, f"{dt:%Y}", f"{dt:%Y-%m}")
    dest_path = os.path.join(dest_dir, base_name)

    status = OrganizeStatus.UNCHANGED if _same_path(sf.path, dest_path) else OrganizeStatus.MOVE

    return OrganizePlan(
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
    )


# ---------------------------------------------------------------------------
def _relocate(
    src: str,
    dest: str,
    dest_dir: str,
    expected_size: int,
    created_dirs: list[str],
    check: Callable[[], bool] | None,
) -> str | None:
    """Same-filesystem atomic rename; falls back to copy -> verify -> remove
    only if source and destination turn out to be on different filesystems
    (``EXDEV``, e.g. a mounted sub-volume inside the root being organised)."""
    real_src = extended_path(src)
    try:
        st = os.stat(real_src)
    except OSError as exc:
        return os_error_message(exc)
    if st.st_size != expected_size:
        return "El archivo cambió desde el análisis; se omite por seguridad."

    try:
        makedirs_tracked(dest_dir, created_dirs)
    except OSError as exc:
        return f"No se pudo crear la carpeta destino: {os_error_message(exc)}"

    real_dest = extended_path(dest)
    if os.path.exists(real_dest):
        return "El destino ya existe (creado por otro programa)."

    try:
        os.replace(real_src, real_dest)
        return None
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            return f"No se pudo mover: {os_error_message(exc)}"

    try:
        expected_sha = hash_file(src, check=check)
    except HashCancelled:
        raise
    except OSError as exc:
        return f"No se pudo leer el archivo: {os_error_message(exc)}"

    return verified_copy_move(
        src,
        dest,
        dest_dir,
        expected_size,
        expected_sha,
        created_dirs,
        check,
        tmp_prefix="pixmatch-organize",
    )


def apply_organize(
    plans: Iterable[OrganizePlan],
    *,
    history=None,
    progress: Callable[[int, int, str], None] | None = None,
    check: Callable[[], bool] | None = None,
) -> OrganizeReport:
    plans = list(plans)
    todo = [p for p in plans if p.will_move]
    outcomes: list[OrganizeOutcome] = []
    created_dirs: list[str] = []
    cancelled = False
    total = len(todo)

    for idx, p in enumerate(todo, start=1):
        if check is not None and not check():
            cancelled = True
            break
        try:
            error = _relocate(p.source_path, p.dest_path, p.dest_dir, p.size, created_dirs, check)
        except HashCancelled:
            cancelled = True
            break
        ok = error is None or error.startswith("Copiado,")
        outcomes.append(OrganizeOutcome(p.source_path, p.dest_path, ok, p.size if ok else 0, error))
        if progress is not None:
            progress(idx, total, os.path.basename(p.source_path))

    _record_history(history, outcomes, plans)
    return OrganizeReport(outcomes=outcomes, created_dirs=created_dirs, cancelled=cancelled)


def undo_organize(
    outcomes: Iterable[OrganizeOutcome],
    *,
    history=None,
    check: Callable[[], bool] | None = None,
) -> OrganizeReport:
    """Move every successfully-relocated file back to its original path."""
    results: list[OrganizeOutcome] = []
    for o in outcomes:
        if not o.ok:
            continue
        if check is not None and not check():
            break
        src_dir = os.path.dirname(o.source_path)
        try:
            error = _relocate(
                o.dest_path, o.source_path, src_dir, safe_size(o.dest_path), [], check
            )
        except HashCancelled:
            break
        if error is None:
            prune_empty_parents(os.path.dirname(o.dest_path))
        results.append(OrganizeOutcome(o.dest_path, o.source_path, error is None, o.size, error))
    _record_history(history, results, [])
    return OrganizeReport(outcomes=results)


def _record_history(history, outcomes: list[OrganizeOutcome], plans: list[OrganizePlan]) -> None:
    if history is None or not outcomes:
        return
    try:
        from app.database.history import OperationEntry

        by_src = {p.source_path: p for p in plans} if plans else {}
        for o in outcomes:
            plan = by_src.get(o.source_path)
            detail = o.error if o.error else f"{o.source_path} → {o.dest_path}"
            history.record(
                OperationEntry.now(
                    path=o.dest_path or o.source_path,
                    action="organize",
                    result="ok" if o.ok else "error",
                    detail=detail,
                    size=o.size or (plan.size if plan else None),
                )
            )
    except Exception as exc:
        log.warning("No se pudo registrar la reorganización: %s", exc)
