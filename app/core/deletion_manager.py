"""Safe deletion of user-selected files.

Design rules (non-negotiable):
  * Nothing is deleted without an explicit, confirmed user action upstream.
  * The default and strongly-preferred action is **move to the OS recycle
    bin** (via Send2Trash). Permanent deletion is a separate, explicit mode.
  * Every file is re-checked immediately before deletion (exists, is a regular
    file, size still matches the analysis). Anything suspicious is skipped and
    reported, never force-deleted.
  * Every attempt - success or failure - is written to the operation history.
  * The caller decides whether a group may be left with zero copies; this
    module only *flags* it.
"""
from __future__ import annotations

import os
import stat as stat_module
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Optional

from app.core.duplicate_groups import DuplicateGroup, FileRecord
from app.database.history import OperationEntry, OperationHistory
from app.utils.file_utils import extended_path
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


class DeletionMode(str, Enum):
    TRASH = "trash"
    PERMANENT = "permanent"

    @property
    def label(self) -> str:
        return "papelera" if self is DeletionMode.TRASH else "eliminación permanente"


@dataclass
class PlannedDeletion:
    record: FileRecord
    group_id: int
    category: str


@dataclass
class DeletionWarning:
    path: str
    message: str


@dataclass
class DeletionPreview:
    items: list[PlannedDeletion]
    warnings: list[DeletionWarning]
    total_bytes: int
    groups_left_empty: list[int]

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass
class DeletionOutcome:
    path: str
    action: str
    ok: bool
    freed_bytes: int
    error: Optional[str] = None


@dataclass
class DeletionReport:
    mode: DeletionMode
    outcomes: list[DeletionOutcome]
    cancelled: bool = False

    @property
    def succeeded(self) -> list[DeletionOutcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def failed(self) -> list[DeletionOutcome]:
        return [o for o in self.outcomes if not o.ok]

    @property
    def freed_bytes(self) -> int:
        return sum(o.freed_bytes for o in self.succeeded)


def build_preview(groups: Iterable[DuplicateGroup]) -> DeletionPreview:
    """Collect everything marked for deletion and pre-flight it."""
    items: list[PlannedDeletion] = []
    warnings: list[DeletionWarning] = []
    empty: list[int] = []
    total = 0

    for group in groups:
        if group.ignored:
            continue
        marked = [r for r in group.marked_for_deletion if not r.deleted]
        if not marked:
            continue
        survivors = [
            r for r in group.records
            if not r.deleted and r.decision.value != "delete"
        ]
        if not survivors:
            empty.append(group.group_id)

        for rec in marked:
            items.append(PlannedDeletion(rec, group.group_id, group.category.label))
            total += rec.size
            warnings.extend(_preflight(rec))

    return DeletionPreview(items, warnings, total, empty)


def build_preview_for(
    pairs: list[tuple], target: Optional[FileRecord] = None
) -> DeletionPreview:
    """Preview for an explicit list of ``(record, group)`` pairs (used for the
    single-file "Eliminar" action on a card / in the viewer)."""
    from collections import defaultdict

    items: list[PlannedDeletion] = []
    warnings: list[DeletionWarning] = []
    total = 0
    to_delete: dict[int, set[int]] = defaultdict(set)
    group_by_id: dict[int, object] = {}

    for rec, group in pairs:
        if rec.deleted:
            continue
        gid = group.group_id if group is not None else 0
        cat = group.category.label if group is not None else "—"
        items.append(PlannedDeletion(rec, gid, cat))
        total += rec.size
        warnings.extend(_preflight(rec))
        if group is not None:
            to_delete[gid].add(id(rec))
            group_by_id[gid] = group

    empty: list[int] = []
    for gid, ids in to_delete.items():
        group = group_by_id[gid]
        survivors = [
            r for r in group.records
            if not r.deleted and id(r) not in ids and r.decision.value != "delete"
        ]
        if not survivors:
            empty.append(gid)
    return DeletionPreview(items, warnings, total, empty)


def _preflight(rec: FileRecord) -> list[DeletionWarning]:
    try:
        st = os.lstat(extended_path(rec.path))
    except FileNotFoundError:
        return [DeletionWarning(rec.path, "El archivo ya no existe; se omitirá.")]
    except OSError as exc:
        return [DeletionWarning(rec.path, f"No accesible ({exc}); se omitirá.")]

    if stat_module.S_ISDIR(st.st_mode):
        return [DeletionWarning(rec.path, "Es una carpeta; se omitirá.")]
    if stat_module.S_ISLNK(st.st_mode):
        return [DeletionWarning(rec.path, "Es un enlace simbólico; se moverá solo el enlace.")]
    try:
        if os.path.getsize(extended_path(rec.path)) != rec.size:
            return [DeletionWarning(
                rec.path,
                "El tamaño cambió desde el análisis; se omitirá por seguridad.",
            )]
    except OSError as exc:
        return [DeletionWarning(rec.path, f"No accesible ({exc}); se omitirá.")]
    return []


class DeletionManager:
    def __init__(self, history: Optional[OperationHistory] = None) -> None:
        self.history = history or OperationHistory()

    # ------------------------------------------------------------------
    def delete(
        self,
        planned: list[PlannedDeletion],
        *,
        mode: DeletionMode,
        progress: Optional[Callable[[int, int, str], None]] = None,
        check: Optional[Callable[[], bool]] = None,
    ) -> DeletionReport:
        outcomes: list[DeletionOutcome] = []
        entries: list[OperationEntry] = []
        total = len(planned)
        cancelled = False

        for index, item in enumerate(planned, start=1):
            if check is not None and not check():
                cancelled = True
                break

            rec = item.record
            outcome = self._delete_one(rec, mode)
            outcomes.append(outcome)
            entries.append(OperationEntry.now(
                path=rec.path,
                action=mode.value,
                result="ok" if outcome.ok else "error",
                detail=outcome.error,
                size=rec.size,
                group_id=item.group_id,
                category=item.category,
            ))
            if outcome.ok:
                rec.deleted = True

            if progress is not None:
                progress(index, total, rec.path)

        self.history.record_many(entries)
        return DeletionReport(mode=mode, outcomes=outcomes, cancelled=cancelled)

    # ------------------------------------------------------------------
    def _delete_one(self, rec: FileRecord, mode: DeletionMode) -> DeletionOutcome:
        path = rec.path
        real = extended_path(path)
        try:
            st = os.lstat(real)
        except FileNotFoundError:
            return DeletionOutcome(path, mode.value, False, 0, "El archivo ya no existe.")
        except OSError as exc:
            return DeletionOutcome(path, mode.value, False, 0, f"No accesible: {exc}")

        if stat_module.S_ISDIR(st.st_mode):
            return DeletionOutcome(path, mode.value, False, 0, "Es una carpeta; no se elimina.")

        # Narrow the TOCTOU window: re-check size right before acting (skip for
        # symlinks, where we only remove the link itself).
        if not stat_module.S_ISLNK(st.st_mode):
            try:
                if os.path.getsize(real) != rec.size:
                    return DeletionOutcome(
                        path, mode.value, False, 0,
                        "El archivo cambió desde el análisis; se omite por seguridad.",
                    )
            except OSError as exc:
                return DeletionOutcome(path, mode.value, False, 0, f"No accesible: {exc}")

        try:
            if mode is DeletionMode.TRASH:
                _send_to_trash(path)
            else:
                os.remove(real)
        except Exception as exc:  # noqa: BLE001
            log.warning("Delete failed (%s): %s -> %s", mode.value, path, exc)
            return DeletionOutcome(path, mode.value, False, 0, str(exc))

        return DeletionOutcome(path, mode.value, True, rec.size)


def _send_to_trash(path: str) -> None:
    try:
        from send2trash import send2trash
    except ImportError as exc:  # pragma: no cover - dependency present in requirements
        raise RuntimeError(
            "La librería 'Send2Trash' no está instalada, no se puede usar la "
            "papelera del sistema. Instálala con:  pip install Send2Trash"
        ) from exc
    send2trash(os.fspath(path))
