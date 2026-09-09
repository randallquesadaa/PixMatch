"""Perceptual hashing for images: aHash, dHash and pHash (pHash is the
primary method), plus Hamming-distance helpers and a BK-tree for fast
nearest-neighbour queries.

Implemented with only Pillow (no numpy): the images are reduced to a tiny
grayscale grid before hashing, so the cost per image is dominated by Pillow's
resize, not by the pure-Python maths.

A hash is a 64-bit integer. ``None`` means "could not be computed" (corrupt or
unsupported image) and callers must treat that as "no perceptual information",
never as "matches everything".
"""

from __future__ import annotations

import io
import math
from collections.abc import Iterable

from PIL import Image, ImageOps

from app.utils.file_utils import extended_path

HASH_BITS = 64
BHASH_BITS = 256  # 16x16 block-mean "visual layout" hash
_HASH_EDGE = 8  # 8x8 -> 64 bits
_BHASH_EDGE = 16  # 16x16 -> 256 bits
_PHASH_SCALE = 32  # pHash works on a 32x32 image, keeps the 8x8 low freqs

# cosine lookup tables, built lazily and cached per size
_cos_tables: dict[int, list[list[float]]] = {}


def _cos_table(n: int) -> list[list[float]]:
    table = _cos_tables.get(n)
    if table is None:
        table = [[math.cos(math.pi / n * (x + 0.5) * k) for x in range(n)] for k in range(n)]
        _cos_tables[n] = table
    return table


def _open_gray(path: str, size: tuple[int, int]) -> list[int] | None:
    """Return the pixels of *path* reduced to ``size`` grayscale, row-major."""
    try:
        with Image.open(extended_path(path)) as img:
            img = ImageOps.exif_transpose(img)  # normalise rotation
            img = img.convert("L").resize(size, Image.LANCZOS)
            return list(img.getdata())
    except Exception:
        return None


def _load_gray_master(path: str):
    """Decode once, return an EXIF-normalised grayscale PIL image (or None)."""
    try:
        with Image.open(extended_path(path)) as img:
            return ImageOps.exif_transpose(img).convert("L")
    except Exception:
        return None


def _gray_master_from_bytes(data: bytes):
    try:
        with Image.open(io.BytesIO(data)) as img:
            return ImageOps.exif_transpose(img).convert("L")
    except Exception:
        return None


def perceptual_hash_bytes(data: bytes) -> int | None:
    """pHash of an in-memory image (e.g. a video frame piped from ffmpeg)."""
    master = _gray_master_from_bytes(data)
    return _phash_from(master) if master is not None else None


def _ahash_from(master) -> int:
    px = list(master.resize((_HASH_EDGE, _HASH_EDGE), Image.LANCZOS).getdata())
    avg = sum(px) / len(px)
    return _bits_to_int(p > avg for p in px)


def _dhash_from(master) -> int:
    w = _HASH_EDGE + 1
    px = list(master.resize((w, _HASH_EDGE), Image.LANCZOS).getdata())
    bits = []
    for row in range(_HASH_EDGE):
        base = row * w
        for col in range(_HASH_EDGE):
            bits.append(px[base + col] < px[base + col + 1])
    return _bits_to_int(bits)


def _bhash_from(master) -> int:
    """16x16 block-mean layout hash (256 bits). Spatial, not frequency - it
    tells apart images with a genuinely different composition even when their
    global pHash happens to be close (e.g. two different sunsets)."""
    n = _BHASH_EDGE
    px = list(master.resize((n, n), Image.LANCZOS).getdata())
    ordered = sorted(px)
    mid = len(ordered) // 2
    median = (ordered[mid] + ordered[~mid]) / 2
    return _bits_to_int(v > median for v in px)


def bhash(path: str) -> int | None:
    master = _load_gray_master(path)
    return _bhash_from(master) if master is not None else None


def _phash_from(master) -> int:
    n, e = _PHASH_SCALE, _HASH_EDGE
    px = list(master.resize((n, n), Image.LANCZOS).getdata())
    cos = _cos_table(n)
    rows = [px[i * n : (i + 1) * n] for i in range(n)]
    tmp = [
        [math.fsum(rows[x][y] * cos[u][y] for y in range(n)) for u in range(e)] for x in range(n)
    ]
    dct = [[math.fsum(tmp[x][u] * cos[v][x] for x in range(n)) for u in range(e)] for v in range(e)]
    vals = [dct[v][u] for v in range(e) for u in range(e)]
    ordered = sorted(vals)
    mid = len(ordered) // 2
    median = (ordered[mid] + ordered[~mid]) / 2
    return _bits_to_int(v > median for v in vals)


def _bits_to_int(bits: Iterable[bool]) -> int:
    value = 0
    for i, bit in enumerate(bits):
        if bit:
            value |= 1 << i
    return value


def average_hash(path: str) -> int | None:
    px = _open_gray(path, (_HASH_EDGE, _HASH_EDGE))
    if px is None:
        return None
    avg = sum(px) / len(px)
    return _bits_to_int(p > avg for p in px)


def difference_hash(path: str) -> int | None:
    # 9x8: compare each pixel with its right neighbour -> 8x8 bits
    px = _open_gray(path, (_HASH_EDGE + 1, _HASH_EDGE))
    if px is None:
        return None
    w = _HASH_EDGE + 1
    bits = []
    for row in range(_HASH_EDGE):
        base = row * w
        for col in range(_HASH_EDGE):
            bits.append(px[base + col] < px[base + col + 1])
    return _bits_to_int(bits)


def perceptual_hash(path: str) -> int | None:
    """pHash: 2D DCT of a 32x32 grayscale image, threshold the 8x8 low
    frequencies against their median."""
    px = _open_gray(path, (_PHASH_SCALE, _PHASH_SCALE))
    if px is None:
        return None

    n = _PHASH_SCALE
    e = _HASH_EDGE
    cos = _cos_table(n)
    rows = [px[i * n : (i + 1) * n] for i in range(n)]

    # separable DCT-II, keeping only the first `e` frequencies per axis
    # tmp[x][u] = sum_y rows[x][y] * cos[u][y]
    tmp = [
        [math.fsum(rows[x][y] * cos[u][y] for y in range(n)) for u in range(e)] for x in range(n)
    ]
    # dct[v][u] = sum_x tmp[x][u] * cos[v][x]
    dct = [[math.fsum(tmp[x][u] * cos[v][x] for x in range(n)) for u in range(e)] for v in range(e)]

    vals = [dct[v][u] for v in range(e) for u in range(e)]
    ordered = sorted(vals)
    mid = len(ordered) // 2
    median = (ordered[mid] + ordered[~mid]) / 2  # exclude the DC term's pull a bit
    return _bits_to_int(v > median for v in vals)


def all_hashes(path: str) -> tuple[int | None, int | None, int | None]:
    """(phash, dhash, ahash) from a single decode."""
    master = _load_gray_master(path)
    if master is None:
        return None, None, None
    return _phash_from(master), _dhash_from(master), _ahash_from(master)


def hashes_from_gray(master) -> tuple[int, int, int, int]:
    """(phash, dhash, ahash, bhash) from an already-decoded grayscale image."""
    return (
        _phash_from(master),
        _dhash_from(master),
        _ahash_from(master),
        _bhash_from(master),
    )


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def similarity_percent(distance: int, bits: int = HASH_BITS) -> float:
    return round(100.0 * (1.0 - distance / bits), 1)


# ---------------------------------------------------------------------------
class BKTree:
    """Burkhard-Keller tree over Hamming distance for radius queries."""

    __slots__ = ("_root",)

    def __init__(self) -> None:
        self._root: list | None = None  # [key, payloads, {dist: child_node}]

    def add(self, key: int, payload) -> None:
        if self._root is None:
            self._root = [key, [payload], {}]
            return
        node = self._root
        while True:
            d = hamming(key, node[0])
            if d == 0:
                node[1].append(payload)
                return
            child = node[2].get(d)
            if child is None:
                node[2][d] = [key, [payload], {}]
                return
            node = child

    def query(self, key: int, radius: int) -> list:
        if self._root is None:
            return []
        out: list = []
        stack = [self._root]
        while stack:
            node = stack.pop()
            d = hamming(key, node[0])
            if d <= radius:
                out.extend(node[1])
            lo, hi = d - radius, d + radius
            for edge, child in node[2].items():
                if lo <= edge <= hi:
                    stack.append(child)
        return out
