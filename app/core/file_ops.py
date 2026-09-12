"""Generic, safe file-relocation primitives shared by the import and organise
tools (and anything else that needs to move a file without ever silently
losing or overwriting data).

The core guarantee is :func:`verified_copy_move`: copy to a temp file at the
destination, verify its SHA-256, then and only then replace and remove the
source. If any step fails the source is left untouched.
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable

from app.core.hashing import HashCancelled, hash_file
from app.utils.file_utils import extended_path

_COPY_CHUNK = 1 << 20


def os_error_message(exc: OSError) -> str:
    return exc.strerror or str(exc)


def silent_remove(path: str) -> None:
    """Best-effort cleanup of a leftover temp file after a failed copy. The
    caller already has its own error to report; whether this succeeds or not
    changes nothing for the source file, which is what actually matters."""
    try:
        os.remove(extended_path(path))
    except OSError:
        pass


def copy_stream(src_real: str, dst_real: str, check: Callable[[], bool] | None) -> None:
    with open(src_real, "rb", buffering=0) as fi, open(dst_real, "wb", buffering=0) as fo:
        while True:
            if check is not None and not check():
                raise HashCancelled()
            block = fi.read(_COPY_CHUNK)
            if not block:
                break
            fo.write(block)


def safe_size(path: str) -> int:
    try:
        return os.path.getsize(extended_path(path))
    except OSError:
        return -1


def prune_empty_parents(directory: str, *, max_up: int = 3) -> None:
    """Remove *directory* and its parents while they are empty (at most
    ``max_up`` levels: e.g. the <YYYY-MM>, <YYYY> and <country> folders left
    behind by an undone move)."""
    for _ in range(max_up):
        try:
            os.rmdir(extended_path(directory))
        except OSError:
            return
        directory = os.path.dirname(directory)


def makedirs_tracked(directory: str, created: list[str]) -> None:
    """``os.makedirs(directory)``, appending every folder it actually had to
    create (outermost first) to *created* -- so a failed operation can tell
    the caller what it added versus what was already there."""
    if os.path.isdir(directory):
        return
    parts: list[str] = []
    cur = directory
    while cur and not os.path.isdir(extended_path(cur)):
        parts.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    os.makedirs(extended_path(directory), exist_ok=True)
    created.extend(reversed(parts))


def verified_copy_move(
    src: str,
    dest: str,
    dest_dir: str,
    expected_size: int,
    expected_sha: str | None,
    created_dirs: list[str],
    check: Callable[[], bool] | None,
    *,
    tmp_prefix: str = "pixmatch",
) -> str | None:
    """Copy *src* to *dest* (via a temp file in *dest_dir*), verify its
    SHA-256 against *expected_sha* (skipped if ``None``), then replace and
    remove the source. Returns an error string, or ``None`` on success. The
    source is left intact on any failure -- safe even across filesystems."""
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

    if os.path.exists(extended_path(dest)):
        return "El destino ya existe (creado por otro programa)."

    tmp = os.path.join(dest_dir, f".{tmp_prefix}-{uuid.uuid4().hex}{os.path.splitext(dest)[1]}")
    real_tmp = extended_path(tmp)
    try:
        copy_stream(real_src, real_tmp, check)
    except HashCancelled:
        silent_remove(tmp)
        raise
    except OSError as exc:
        silent_remove(tmp)
        return f"No se pudo copiar: {os_error_message(exc)}"

    if expected_sha is not None:
        try:
            if hash_file(tmp) != expected_sha:
                silent_remove(tmp)
                return "La copia no coincide (verificación SHA-256 fallida)."
        except OSError as exc:
            silent_remove(tmp)
            return f"No se pudo verificar la copia: {os_error_message(exc)}"

    try:
        shutil.copystat(real_src, real_tmp)
    except OSError:
        # Preserving mtime/permissions is a nice-to-have, not a correctness
        # requirement -- the move still proceeds and the file still lands
        # intact even if the filesystem refuses to copy metadata (e.g. FAT).
        pass

    try:
        os.replace(real_tmp, extended_path(dest))
    except OSError as exc:
        silent_remove(tmp)
        return f"No se pudo colocar el archivo: {os_error_message(exc)}"

    try:
        os.remove(real_src)
    except OSError as exc:
        return f"Copiado, pero no se pudo borrar el origen: {os_error_message(exc)}"
    return None
