"""Rename photos / videos to a date-based name.

Name = the capture date, taken from (in order):
  1. EXIF DateTimeOriginal
  2. EXIF DateTimeDigitized
  3. EXIF DateTime
  4. video creation_time (via ffprobe/ffmpeg, if available)
  5. the file's modification time (always available - the fallback)

Default pattern ``%Y%m%d_%H%M%S`` -> ``20221202_143005.jpg``.

Safety:
  * files are **only renamed**, never moved to another folder
  * an existing name is never overwritten - a numeric suffix is added
    (``…_2.jpg``, ``…_3.jpg``) deterministically
  * the rename is two-phase (via a temp name) so swaps / cycles are safe
  * every rename is written to the operation history and can be undone
"""

from __future__ import annotations

import os
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from PIL import Image

from app.utils.file_utils import extended_path
from app.utils.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_PATTERN = "%Y%m%d_%H%M%S"
# (strftime pattern, example) - shown in the pattern dropdown
PATTERN_PRESETS = [
    ("%Y%m%d_%H%M%S", "20221202_143005"),
    ("%Y-%m-%d_%H-%M-%S", "2022-12-02_14-30-05"),
    ("%Y%m%d-%H%M%S", "20221202-143005"),
    ("%Y_%m_%d_%H_%M_%S", "2022_12_02_14_30_05"),
]

_EXIF_DATETIME = 0x0132  # 306  DateTime
_EXIF_IFD = 0x8769  # ExifOffset
_EXIF_DT_ORIGINAL = 0x9003  # 36867 DateTimeOriginal
_EXIF_DT_DIGITIZED = 0x9004  # 36868 DateTimeDigitized


class DateSource(str, Enum):
    EXIF_ORIGINAL = "exif_original"
    EXIF_DIGITIZED = "exif_digitized"
    EXIF_DATETIME = "exif_datetime"
    VIDEO_CREATION = "video_creation"
    FILE_MTIME = "file_mtime"

    @property
    def label(self) -> str:
        return {
            DateSource.EXIF_ORIGINAL: "EXIF · fecha de captura",
            DateSource.EXIF_DIGITIZED: "EXIF · fecha de digitalización",
            DateSource.EXIF_DATETIME: "EXIF · fecha",
            DateSource.VIDEO_CREATION: "metadatos del vídeo",
            DateSource.FILE_MTIME: "fecha de modificación del archivo",
        }[self]

    @property
    def is_metadata(self) -> bool:
        return self is not DateSource.FILE_MTIME


class RenameStatus(str, Enum):
    RENAME = "rename"
    SUFFIXED = "suffixed"  # rename + a numeric suffix to avoid a clash
    UNCHANGED = "unchanged"  # already has the right name
    ERROR = "error"

    @property
    def label(self) -> str:
        return {
            RenameStatus.RENAME: "se renombrará",
            RenameStatus.SUFFIXED: "se renombrará (con sufijo)",
            RenameStatus.UNCHANGED: "sin cambios",
            RenameStatus.ERROR: "error",
        }[self]


@dataclass
class RenamePlan:
    path: str
    directory: str
    old_name: str
    new_name: str
    source: DateSource
    timestamp: datetime | None
    status: RenameStatus
    error: str | None = None
    enabled: bool = True

    @property
    def new_path(self) -> str:
        return os.path.join(self.directory, self.new_name)

    @property
    def will_change(self) -> bool:
        return self.enabled and self.status in (RenameStatus.RENAME, RenameStatus.SUFFIXED)


@dataclass
class RenameOutcome:
    path: str
    old_name: str
    new_name: str
    ok: bool
    error: str | None = None


@dataclass
class RenameReport:
    outcomes: list[RenameOutcome]
    cancelled: bool = False

    @property
    def succeeded(self) -> list[RenameOutcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def failed(self) -> list[RenameOutcome]:
        return [o for o in self.outcomes if not o.ok]


# ---------------------------------------------------------------------------
def parse_datetime_string(value) -> datetime | None:
    """Parse an EXIF ``YYYY:MM:DD HH:MM:SS`` / ISO-8601 timestamp, tolerating a
    sub-second or timezone tail. ``None`` if it cannot be read. Shared with the
    import/organise tool (for video ``creation_time``)."""
    return _parse_exif_datetime(value)


def _parse_exif_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "replace")
    text = str(value).strip().strip("\x00").strip()
    if not text or text.startswith(("0000", "    ")):
        return None
    # EXIF is "YYYY:MM:DD HH:MM:SS"; drop any sub-second / timezone tail
    core = text.replace("T", " ").split(".")[0].split("+")[0].strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d %H:%M", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(core, fmt)
        except ValueError:
            continue
    try:
        d, _, t = core.partition(" ")
        return datetime.fromisoformat(f"{d.replace(':', '-')} {t}")
    except ValueError:
        return None


def _exif_datetimes(path: str) -> dict[DateSource, datetime]:
    out: dict[DateSource, datetime] = {}
    try:
        with Image.open(extended_path(path)) as img:
            exif = img.getexif()
    except Exception:
        return out
    if not exif:
        return out
    dt = _parse_exif_datetime(exif.get(_EXIF_DATETIME))
    if dt:
        out[DateSource.EXIF_DATETIME] = dt
    try:
        sub = exif.get_ifd(_EXIF_IFD)
    except Exception:
        sub = {}
    if sub:
        dto = _parse_exif_datetime(sub.get(_EXIF_DT_ORIGINAL))
        if dto:
            out[DateSource.EXIF_ORIGINAL] = dto
        dtd = _parse_exif_datetime(sub.get(_EXIF_DT_DIGITIZED))
        if dtd:
            out[DateSource.EXIF_DIGITIZED] = dtd
    return out


def _video_datetime(path: str, ffmpeg_tools) -> datetime | None:
    if ffmpeg_tools is None or not getattr(ffmpeg_tools, "available", False):
        return None
    try:
        from app.core.video_analyzer import probe_video

        info = probe_video(path, ffmpeg_tools)
        return _parse_exif_datetime(info.creation_time) if info.creation_time else None
    except Exception:
        return None


def capture_datetime(
    path: str, kind: str, mtime: float, *, ffmpeg_tools=None
) -> tuple[datetime, DateSource]:
    """Best capture datetime + where it came from. Always returns something
    (falls back to the file mtime)."""
    if kind == "image":
        found = _exif_datetimes(path)
        for src in (DateSource.EXIF_ORIGINAL, DateSource.EXIF_DIGITIZED, DateSource.EXIF_DATETIME):
            if src in found:
                return found[src], src
    elif kind == "video":
        dt = _video_datetime(path, ffmpeg_tools)
        if dt is not None:
            return dt, DateSource.VIDEO_CREATION
    return datetime.fromtimestamp(mtime), DateSource.FILE_MTIME


# ---------------------------------------------------------------------------
def format_name(dt: datetime, pattern: str, prefix: str, ext: str) -> str:
    """Build a file name from a timestamp: ``{prefix}{dt:pattern}{ext}``.

    Invalid path characters are stripped; a bad pattern falls back to
    :data:`DEFAULT_PATTERN`. Shared with the import/organise tool.
    """
    pattern = pattern.replace("/", "-").replace("\\", "-").strip() or DEFAULT_PATTERN
    try:
        base = dt.strftime(pattern)
    except (ValueError, KeyError):
        base = dt.strftime(DEFAULT_PATTERN)
    base = "".join(c for c in base if c not in '<>:"|?*\x00').strip()
    return f"{prefix}{base}{ext}"


def build_rename_plan(
    files: Iterable,  # objects with .path, .kind (FileKind), .mtime
    *,
    pattern: str = DEFAULT_PATTERN,
    prefix: str = "",
    lowercase_ext: bool = True,
    normalise_jpeg: bool = True,
    ffmpeg_tools=None,
    check: Callable[[], bool] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> list[RenamePlan]:
    files = list(files)
    plans: list[RenamePlan] = []
    for i, f in enumerate(files):
        if check is not None and not check():
            break
        kind = f.kind.value if hasattr(f.kind, "value") else str(f.kind)
        try:
            dt, source = capture_datetime(f.path, kind, f.mtime, ffmpeg_tools=ffmpeg_tools)
        except Exception as exc:
            plans.append(
                RenamePlan(
                    path=f.path,
                    directory=os.path.dirname(f.path),
                    old_name=os.path.basename(f.path),
                    new_name=os.path.basename(f.path),
                    source=DateSource.FILE_MTIME,
                    timestamp=None,
                    status=RenameStatus.ERROR,
                    error=str(exc),
                )
            )
            continue

        old_name = os.path.basename(f.path)
        stem_ext = os.path.splitext(old_name)[1]
        ext = stem_ext.lower() if lowercase_ext else stem_ext
        if normalise_jpeg and ext in (".jpeg", ".jpe", ".jfif"):
            ext = ".jpg"
        new_name = format_name(dt, pattern, prefix, ext)
        status = RenameStatus.UNCHANGED if new_name == old_name else RenameStatus.RENAME
        plans.append(
            RenamePlan(
                path=f.path,
                directory=os.path.dirname(f.path),
                old_name=old_name,
                new_name=new_name,
                source=source,
                timestamp=dt,
                status=status,
            )
        )
        if on_progress is not None and (i % 50 == 0 or i == len(files) - 1):
            on_progress(i + 1, len(files))

    _resolve_conflicts(plans)
    return plans


def _resolve_conflicts(plans: list[RenamePlan]) -> None:
    """Give every target name a unique spot in its directory."""
    by_dir: dict[str, list[RenamePlan]] = defaultdict(list)
    for p in plans:
        by_dir[p.directory].append(p)

    for directory, dir_plans in by_dir.items():
        try:
            on_disk = {n.lower() for n in os.listdir(directory)}
        except OSError:
            on_disk = set()
        # the current names of the plans will be freed by the rename
        for p in dir_plans:
            on_disk.discard(p.old_name.lower())

        taken = set(on_disk)
        # unchanged first (they keep their name no matter what), then the rest
        # in a stable order so suffixes are deterministic across runs
        ordered = sorted(
            dir_plans,
            key=lambda p: (
                0 if p.status is RenameStatus.UNCHANGED else 1,
                p.timestamp or datetime.min,
                p.old_name.lower(),
            ),
        )
        for p in ordered:
            if p.status in (RenameStatus.ERROR, RenameStatus.UNCHANGED):
                taken.add(p.new_name.lower())
                continue
            stem, ext = os.path.splitext(p.new_name)
            candidate = p.new_name
            n = 1
            while candidate.lower() in taken:
                n += 1
                candidate = f"{stem}_{n}{ext}"
            if candidate != p.new_name:
                p.new_name = candidate
                p.status = RenameStatus.SUFFIXED
            taken.add(candidate.lower())


# ---------------------------------------------------------------------------
def apply_renames(
    plans: Iterable[RenamePlan],
    *,
    history=None,
    progress: Callable[[int, int, str], None] | None = None,
    check: Callable[[], bool] | None = None,
) -> RenameReport:
    todo = [p for p in plans if p.will_change and p.old_name != p.new_name]
    outcomes: list[RenameOutcome] = []
    cancelled = False

    # -- phase 1: everything -> a unique temp name (handles swaps/cycles) --
    staged: list[tuple[RenamePlan, str]] = []
    for p in todo:
        if check is not None and not check():
            cancelled = True
            break
        tmp = os.path.join(
            p.directory, f".pixmatch-rename-{uuid.uuid4().hex}{os.path.splitext(p.old_name)[1]}"
        )
        try:
            os.rename(extended_path(p.path), extended_path(tmp))
            staged.append((p, tmp))
        except OSError as exc:
            outcomes.append(RenameOutcome(p.path, p.old_name, p.new_name, False, _os_error(exc)))

    # -- phase 2: temp -> final name --
    total = len(staged)
    for i, (p, tmp) in enumerate(staged, start=1):
        final = p.new_path
        try:
            if os.path.exists(extended_path(final)):
                os.rename(extended_path(tmp), extended_path(p.path))  # roll back
                outcomes.append(
                    RenameOutcome(
                        p.path,
                        p.old_name,
                        p.new_name,
                        False,
                        "El destino ya existe (creado por otro programa).",
                    )
                )
            else:
                os.rename(extended_path(tmp), extended_path(final))
                outcomes.append(RenameOutcome(p.path, p.old_name, p.new_name, True))
        except OSError as exc:
            try:
                os.rename(extended_path(tmp), extended_path(p.path))
            except OSError:
                outcomes.append(
                    RenameOutcome(
                        p.path,
                        p.old_name,
                        p.new_name,
                        False,
                        f"{_os_error(exc)} — el archivo quedó como «{os.path.basename(tmp)}»",
                    )
                )
                continue
            outcomes.append(RenameOutcome(p.path, p.old_name, p.new_name, False, _os_error(exc)))
        if progress is not None:
            progress(i, total, p.new_name)

    _record_history(history, outcomes)
    return RenameReport(outcomes=outcomes, cancelled=cancelled)


def undo_renames(
    outcomes: Iterable[RenameOutcome],
    *,
    history=None,
    check: Callable[[], bool] | None = None,
) -> RenameReport:
    """Rename the successful outcomes back to their original names."""
    inverse: list[RenamePlan] = []
    for o in outcomes:
        if not o.ok:
            continue
        directory = os.path.dirname(o.path)
        inverse.append(
            RenamePlan(
                path=os.path.join(directory, o.new_name),
                directory=directory,
                old_name=o.new_name,
                new_name=o.old_name,
                source=DateSource.FILE_MTIME,
                timestamp=None,
                status=RenameStatus.RENAME,
            )
        )
    return apply_renames(inverse, history=history, check=check)


def _os_error(exc: OSError) -> str:
    return exc.strerror or str(exc)


def _record_history(history, outcomes: list[RenameOutcome]) -> None:
    if history is None:
        return
    try:
        from app.database.history import OperationEntry

        for o in outcomes:
            history.record(
                OperationEntry.now(
                    path=o.path if not o.ok else os.path.join(os.path.dirname(o.path), o.new_name),
                    action="rename",
                    result="ok" if o.ok else "error",
                    detail=(o.error if not o.ok else f"{o.old_name} → {o.new_name}"),
                )
            )
    except Exception as exc:
        log.warning("No se pudo registrar el renombrado: %s", exc)
