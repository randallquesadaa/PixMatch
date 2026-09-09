"""Detect when one image is a *crop* (partial framing) of another.

Approach (cheap, grid-based):
  * reduce the larger image to a GRID x GRID map of block means
  * for a range of candidate crop-rectangle sizes (matching the smaller
    image's aspect ratio), slide the smaller image's block map over the
    larger one and take the best normalised correlation
  * a high correlation at some offset -> the small image is that region of
    the big one

Only runs on a bounded set of candidate pairs (same colour palette, different
aspect / not already grouped) and only when the user picks a high sensitivity,
so the O(scales x positions) cost stays negligible.

Never reported as PIXEL IDENTICAL - a crop is a different picture. Category:
CROPPED_SIMILAR.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageOps

from app.utils.file_utils import extended_path
from app.utils.logging_setup import get_logger

log = get_logger(__name__)

_GRID = 32
_MIN_CELLS = 7          # a crop smaller than ~20% of the frame is ignored
_NCC_MATCH = 0.82


@dataclass
class CropResult:
    score: float                 # 0..100
    region: tuple[float, float, float, float]   # (x0,y0,x1,y1) fractions in the big image
    big_is_first: bool


def _grid_means(path: str) -> Optional[tuple[list[float], int, int]]:
    try:
        with Image.open(extended_path(path)) as img:
            oriented = ImageOps.exif_transpose(img).convert("L")
            w, h = oriented.size
            g = oriented.resize((_GRID, _GRID), Image.BOX)
            return [float(p) for p in g.getdata()], w, h
    except Exception as exc:  # noqa: BLE001
        log.debug("crop grid failed for %s: %s", path, exc)
        return None


def _ncc(patch: list[float], window: list[float]) -> float:
    n = len(patch)
    mp = sum(patch) / n
    mw = sum(window) / n
    num = 0.0
    dp = 0.0
    dw = 0.0
    for a, b in zip(patch, window):
        da, db = a - mp, b - mw
        num += da * db
        dp += da * da
        dw += db * db
    denom = math.sqrt(dp * dw)
    return num / denom if denom > 1e-6 else 0.0


def detect_crop(path_a: str, path_b: str) -> Optional[CropResult]:
    ga = _grid_means(path_a)
    gb = _grid_means(path_b)
    if ga is None or gb is None:
        return None

    grid_a, wa, ha = ga
    grid_b, wb, hb = gb
    if wa * ha >= wb * hb:
        big_grid, small_path, sw, sh, big_is_first = grid_a, path_b, wb, hb, True
    else:
        big_grid, small_path, sw, sh, big_is_first = grid_b, path_a, wa, ha, False
    if sw <= 0 or sh <= 0:
        return None
    small_aspect = sw / sh

    best = 0.0
    best_region = (0.0, 0.0, 1.0, 1.0)

    try:
        with Image.open(extended_path(small_path)) as img:
            small_gray = ImageOps.exif_transpose(img).convert("L")
    except Exception:  # noqa: BLE001
        return None

    for cw in range(_MIN_CELLS, _GRID + 1):
        ch = round(cw / small_aspect)
        if ch < _MIN_CELLS or ch > _GRID:
            continue
        patch = [float(p) for p in small_gray.resize((cw, ch), Image.BOX).getdata()]
        for y0 in range(0, _GRID - ch + 1):
            for x0 in range(0, _GRID - cw + 1):
                window = [
                    big_grid[(y0 + r) * _GRID + (x0 + c)]
                    for r in range(ch) for c in range(cw)
                ]
                score = _ncc(patch, window)
                if score > best:
                    best = score
                    best_region = (
                        x0 / _GRID, y0 / _GRID,
                        (x0 + cw) / _GRID, (y0 + ch) / _GRID,
                    )

    if best < _NCC_MATCH:
        return None
    x0, y0, x1, y1 = best_region
    if (x1 - x0) > 0.92 and (y1 - y0) > 0.92:
        return None  # near-full-frame match is a resize/duplicate, not a crop
    return CropResult(score=round(100.0 * best, 1), region=best_region,
                      big_is_first=big_is_first)
