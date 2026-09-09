"""Match categories.

These categories are intentionally kept separate and are never mixed. Two
files are only ever labelled with the *strongest* evidence actually gathered:

  FILE_IDENTICAL      the raw bytes of both files are identical (same SHA-256)
  PIXEL_IDENTICAL     files differ, but the decoded pixels are exactly equal
  VISUALLY_IDENTICAL  same picture, minor technical differences               (phase 2)
  VERY_SIMILAR        very likely a variant of the same picture               (phase 2)
  SIMILAR             looks related                                           (phase 2)
  DIFFERENT           not a duplicate

Phase 1 only ever produces FILE_IDENTICAL groups. The rest of the enum exists
so the rest of the codebase (UI colours, filters, exports) is already built
around the final taxonomy.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class CategoryStyle:
    key: str
    label_es: str
    short_es: str
    color: str      # hex, used by the UI
    emoji: str
    description_es: str


class MatchCategory(Enum):
    FILE_IDENTICAL = CategoryStyle(
        "file_identical", "Archivo idéntico", "IDÉNTICO",
        "#2e7d32", "🟢",
        "Los bytes de los archivos son exactamente iguales (mismo SHA-256).",
    )
    PIXEL_IDENTICAL = CategoryStyle(
        "pixel_identical", "Pixel por pixel idéntico", "PIXEL IDÉNTICO",
        "#1565c0", "🔵",
        "Los archivos difieren, pero la imagen decodificada es exactamente igual.",
    )
    VISUALLY_IDENTICAL = CategoryStyle(
        "visually_identical", "Visualmente idéntico", "VISUALMENTE IDÉNTICO",
        "#00838f", "🟦",
        "Representa la misma imagen con diferencias técnicas menores.",
    )
    SAME_CONTENT_LIKELY = CategoryStyle(
        "same_content_likely", "Mismo contenido probable", "MISMO CONTENIDO PROBABLE",
        "#00838f", "🟦",
        "Los fotogramas muestreados coinciden con mucha probabilidad. No se "
        "garantiza al 100% porque solo se comparan unos pocos fotogramas.",
    )
    RESIZED_DUPLICATE = CategoryStyle(
        "resized_duplicate", "Duplicado redimensionado", "REDIMENSIONADO",
        "#0277bd", "🟦",
        "La misma imagen guardada a otra resolución (o compresión / formato).",
    )
    CROPPED_SIMILAR = CategoryStyle(
        "cropped_similar", "Recorte de la misma imagen", "RECORTE",
        "#ef6c00", "🟧",
        "Una imagen parece ser un recorte (encuadre parcial) de la otra.",
    )
    VERY_SIMILAR = CategoryStyle(
        "very_similar", "Muy similar", "MUY SIMILAR",
        "#f9a825", "🟡",
        "Probablemente una variante de la otra (resolución, compresión, formato, "
        "ligeros cambios de brillo/color/recorte).",
    )
    SIMILAR = CategoryStyle(
        "similar", "Similar", "SIMILAR",
        "#ef6c00", "🟠",
        "Parece la misma escena u objeto, con diferencias mayores. Revísala.",
    )
    DIFFERENT = CategoryStyle(
        "different", "Diferente", "DIFERENTE",
        "#c62828", "🔴",
        "No es un duplicado.",
    )

    @property
    def style(self) -> CategoryStyle:
        return self.value

    @property
    def label(self) -> str:
        return self.value.label_es

    @property
    def color(self) -> str:
        return self.value.color

    @property
    def rank(self) -> int:
        """Strength of the evidence - higher means "more certainly a duplicate"."""
        return _RANK[self]


_RANK = {
    MatchCategory.DIFFERENT: 0,
    MatchCategory.SIMILAR: 1,
    MatchCategory.CROPPED_SIMILAR: 2,
    MatchCategory.VERY_SIMILAR: 2,
    MatchCategory.VISUALLY_IDENTICAL: 3,
    MatchCategory.SAME_CONTENT_LIKELY: 3,
    MatchCategory.RESIZED_DUPLICATE: 4,
    MatchCategory.PIXEL_IDENTICAL: 4,
    MatchCategory.FILE_IDENTICAL: 5,
}

# Categories that mean "the pixels/bytes are provably equal" -> confidence 100%.
_EXACT = {MatchCategory.FILE_IDENTICAL, MatchCategory.PIXEL_IDENTICAL}


def category_confidence(category: "MatchCategory", score: float | None) -> int:
    """A 0-100 confidence for the match label.

    Exact categories are 100 by construction. For the similarity family the
    confidence *is* the combined score (it is an estimate, not a proof)."""
    if category in _EXACT:
        return 100
    if score is None:
        return 0
    return int(round(max(0.0, min(100.0, score))))


def classify_similarity(percent: float, threshold: float) -> MatchCategory:
    """Map a perceptual-similarity percentage to a *similar* category.

    ``threshold`` is the user's minimum similarity to consider images related.
    A perfect perceptual match that is **not** pixel-identical is only ever
    "visually identical", never "pixel identical" - the categories stay
    separate.
    """
    if percent >= 100.0:
        return MatchCategory.VISUALLY_IDENTICAL
    very_similar_cut = (threshold + 100.0) / 2.0
    if percent >= very_similar_cut:
        return MatchCategory.VERY_SIMILAR
    if percent >= threshold:
        return MatchCategory.SIMILAR
    return MatchCategory.DIFFERENT


def distance_cutoff(threshold_percent: float, bits: int = 64) -> int:
    """Largest Hamming distance still counted as `threshold_percent` similar."""
    return max(0, round((1.0 - threshold_percent / 100.0) * bits))


def classify_video_similarity(
    percent: float, threshold: float, same_duration: bool
) -> MatchCategory:
    """Map an average sampled-frame similarity to a *video* category.

    Two videos are never called "identical" from a few frames - the strongest
    verdict is "same content likely".
    """
    if percent >= 98.0 and same_duration:
        return MatchCategory.SAME_CONTENT_LIKELY
    very_similar_cut = (threshold + 100.0) / 2.0
    if percent >= very_similar_cut:
        return MatchCategory.VERY_SIMILAR
    if percent >= threshold:
        return MatchCategory.SIMILAR
    return MatchCategory.DIFFERENT
