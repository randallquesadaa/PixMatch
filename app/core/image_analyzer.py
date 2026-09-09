"""Level 3 detection: decode images and compare the real pixels.

Key distinction (never mixed):
  * FILE_IDENTICAL  - same bytes (handled in :mod:`app.core.hashing`)
  * PIXEL_IDENTICAL - different bytes, but the decoded image is exactly equal

To compare pixels we normalise:
  * EXIF orientation (rotations are applied in memory, the file is untouched)
  * colour: everything is promoted to RGBA so an opaque RGB image and the same
    image saved as RGBA compare equal

:func:`pixel_digest` streams a SHA-256 over the normalised pixels so that
grouping is a cheap dictionary lookup instead of an O(n^2) pairwise decode.
The full-resolution image is opened once and released immediately.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from PIL import Image, ImageChops, ImageOps

from app.utils.file_utils import extended_path
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class DecodedInfo:
    width: int
    height: int
    original_mode: str


def _normalised(img: Image.Image) -> Image.Image:
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return img


def pixel_digest(path: str) -> Optional[tuple[str, DecodedInfo]]:
    """SHA-256 of the normalised pixels, plus basic decoded info.

    Returns ``None`` if the image cannot be decoded.
    """
    try:
        with Image.open(extended_path(path)) as img:
            original_mode = img.mode
            norm = _normalised(img)
            digest = hashlib.sha256()
            digest.update(f"{norm.width}x{norm.height}:RGBA:".encode("ascii"))
            digest.update(norm.tobytes())
            info = DecodedInfo(norm.width, norm.height, original_mode)
            return digest.hexdigest(), info
    except Exception as exc:  # noqa: BLE001
        log.debug("pixel_digest failed for %s: %s", path, exc)
        return None


def pixels_equal(path_a: str, path_b: str) -> bool:
    """Exhaustive check - decode both and compare. Used as a paranoid
    confirmation, e.g. before showing a "PIXEL IDENTICAL" badge."""
    try:
        with Image.open(extended_path(path_a)) as a, Image.open(extended_path(path_b)) as b:
            na, nb = _normalised(a), _normalised(b)
            if na.size != nb.size:
                return False
            # Pillow >= 10 defaults getbbox() to alpha-only for RGBA images;
            # force it to look at every band.
            return ImageChops.difference(na, nb).getbbox(alpha_only=False) is None
    except Exception as exc:  # noqa: BLE001
        log.debug("pixels_equal failed for %s / %s: %s", path_a, path_b, exc)
        return False


def difference_image(
    path_a: str,
    path_b: str,
    *,
    amplify: bool = True,
    max_edge: int = 1400,
) -> Optional[Image.Image]:
    """An RGB image highlighting where A and B differ (B is resized to A).

    For the "Comparación avanzada" view. Returns ``None`` on failure.
    """
    try:
        with Image.open(extended_path(path_a)) as a, Image.open(extended_path(path_b)) as b:
            na = ImageOps.exif_transpose(a).convert("RGB")
            nb = ImageOps.exif_transpose(b).convert("RGB")
            if na.size != nb.size:
                nb = nb.resize(na.size, Image.LANCZOS)
            if max(na.size) > max_edge:
                na.thumbnail((max_edge, max_edge), Image.LANCZOS)
                nb = nb.resize(na.size, Image.LANCZOS)
            diff = ImageChops.difference(na, nb)
            if amplify:
                diff = ImageOps.autocontrast(diff, cutoff=0)
            return diff
    except Exception as exc:  # noqa: BLE001
        log.debug("difference_image failed: %s", exc)
        return None
