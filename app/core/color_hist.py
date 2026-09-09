"""Compact colour-distribution signature.

A 16 x 4 Hue-Saturation histogram (64 bins) computed on a small HSV thumbnail.
Value (brightness) is deliberately ignored so the signature survives mild
brightness / contrast edits, but it still separates images whose *colour
palette* differs - which is what stops two unrelated-but-orange sunsets from
being called duplicates.

Stored as 64 comma-separated integers (0..1000, sums to ~1000). Pure Pillow.
"""

from __future__ import annotations

from PIL import Image, ImageOps

from app.utils.file_utils import extended_path

_THUMB = 48
_H_BINS = 16
_S_BINS = 4
BINS = _H_BINS * _S_BINS
_SCALE = 1000


def signature_from_image(img: Image.Image) -> str:
    hsv = (
        ImageOps.exif_transpose(img)
        .convert("RGB")
        .resize((_THUMB, _THUMB), Image.LANCZOS)
        .convert("HSV")
    )
    counts = [0] * BINS
    total = 0
    for h, s, v in hsv.getdata():
        if v < 12:  # near-black: hue is meaningless
            continue
        hb = (h * _H_BINS) // 256
        sb = (s * _S_BINS) // 256
        counts[hb * _S_BINS + sb] += 1
        total += 1
    if total == 0:
        return ",".join("0" for _ in range(BINS))
    norm = [round(c * _SCALE / total) for c in counts]
    return ",".join(str(x) for x in norm)


def color_signature(path: str) -> str | None:
    try:
        with Image.open(extended_path(path)) as img:
            return signature_from_image(img)
    except Exception:
        return None


def _parse(sig: str) -> list[int]:
    try:
        return [int(x) for x in sig.split(",")]
    except (ValueError, AttributeError):
        return []


def histogram_similarity(sig_a: str | None, sig_b: str | None) -> float | None:
    """Histogram intersection, 0..100. ``None`` if either signature is missing
    or empty."""
    a, b = _parse(sig_a or ""), _parse(sig_b or "")
    if len(a) != BINS or len(b) != BINS:
        return None
    sa, sb = sum(a), sum(b)
    if sa == 0 or sb == 0:
        return None
    inter = sum(min(x, y) for x, y in zip(a, b))
    return round(100.0 * inter / min(sa, sb), 1)
