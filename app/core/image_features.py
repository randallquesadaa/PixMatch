"""All lightweight image signatures from a single decode.

    phash / dhash / ahash : 64-bit perceptual hashes (structure & gradients)
    bhash                 : 256-bit block-mean layout hash (composition)
    color_sig             : 64-bin Hue-Saturation histogram (palette)
    width / height        : oriented pixel dimensions

These feed :mod:`app.core.similarity_engine`, which combines them into one
weighted score. Computing them together avoids decoding the same file five
times.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageOps

from app.core.color_hist import signature_from_image
from app.core.perceptual import hashes_from_gray
from app.utils.file_utils import extended_path
from app.utils.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class ImageSignatures:
    phash: int
    dhash: int
    ahash: int
    bhash: int
    color_sig: str
    width: int
    height: int

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0


def compute_signatures(path: str) -> ImageSignatures | None:
    try:
        with Image.open(extended_path(path)) as img:
            oriented = ImageOps.exif_transpose(img)
            w, h = oriented.size
            gray = oriented.convert("L")
            phash, dhash, ahash, bhash = hashes_from_gray(gray)
            color_sig = signature_from_image(oriented)
            return ImageSignatures(phash, dhash, ahash, bhash, color_sig, w, h)
    except Exception as exc:
        log.debug("signatures failed for %s: %s", path, exc)
        return None
