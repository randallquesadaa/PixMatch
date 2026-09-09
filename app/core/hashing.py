"""File hashing helpers.

* :func:`hash_file` - full streaming cryptographic hash (SHA-256 by default).
  Used to prove two files are byte-for-byte identical.
* :func:`partial_signature` - cheap head+tail+size fingerprint used only to
  avoid full hashes for files that obviously differ. Never used on its own to
  declare a duplicate.
"""
from __future__ import annotations

import hashlib
import os
from typing import Callable, Optional

from app.utils.file_utils import extended_path

_CHUNK = 1 << 20  # 1 MiB
CheckFn = Optional[Callable[[], bool]]


class HashCancelled(Exception):
    pass


def hash_file(
    path: str | os.PathLike[str],
    *,
    algorithm: str = "sha256",
    chunk_size: int = _CHUNK,
    check: CheckFn = None,
) -> str:
    """Return the hex digest of *path*.

    Raises :class:`HashCancelled` if *check* returns ``False`` mid-read,
    or ``OSError`` if the file cannot be read.
    """
    digest = hashlib.new(algorithm)
    with open(extended_path(path), "rb", buffering=0) as fh:
        while True:
            if check is not None and not check():
                raise HashCancelled()
            block = fh.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def partial_signature(
    path: str | os.PathLike[str],
    *,
    edge: int = 64 * 1024,
    algorithm: str = "sha256",
) -> str:
    """A fast fingerprint: size + hash(first `edge` bytes + last `edge` bytes).

    Files with different signatures cannot be identical, so a full hash can be
    skipped. Files with the same signature still get a full hash.
    """
    real = extended_path(path)
    size = os.path.getsize(real)
    digest = hashlib.new(algorithm)
    digest.update(size.to_bytes(8, "little"))
    with open(real, "rb", buffering=0) as fh:
        head = fh.read(edge)
        digest.update(head)
        if size > edge:
            fh.seek(max(edge, size - edge))
            digest.update(fh.read(edge))
    return f"{size}:{digest.hexdigest()}"
