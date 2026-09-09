"""Combined, weighted image-similarity score.

No single algorithm decides. Each signal produces a 0..100 similarity, and the
weighted average is the reported score. The weights are configurable
(``AppConfig.similarity_weights``); the defaults are:

    pHash (structure)        40 %
    dHash (gradients)        20 %
    aHash (coarse tone)      10 %
    colour histogram         10 %
    bHash (composition)      20 %

The engine also returns human-readable *reasons* ("por qué se agruparon") and,
where relevant, an *upgraded category* (RESIZED_DUPLICATE / CROPPED_SIMILAR).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.color_hist import histogram_similarity
from app.core.perceptual import BHASH_BITS, HASH_BITS, hamming, similarity_percent
from app.core.similarity import MatchCategory

DEFAULT_WEIGHTS: dict[str, float] = {
    "phash": 0.40,
    "dhash": 0.20,
    "ahash": 0.10,
    "color": 0.10,
    "bhash": 0.20,
}

# how similar a single signal must be before it counts as a "reason"
_REASON_CUTS = {
    "phash": 90.0,
    "dhash": 88.0,
    "ahash": 88.0,
    "color": 82.0,
    "bhash": 86.0,
}
_REASON_TEXT = {
    "phash": "estructura visual (pHash) muy parecida",
    "dhash": "gradientes y bordes (dHash) coinciden",
    "ahash": "tono general (aHash) coincide",
    "color": "distribución de color muy parecida",
    "bhash": "misma composición por bloques",
}


@dataclass
class Signals:
    """The per-file signatures the engine needs (a thin view over FileRecord)."""
    phash: Optional[int] = None
    dhash: Optional[int] = None
    ahash: Optional[int] = None
    bhash: Optional[int] = None
    color_sig: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None

    @property
    def aspect(self) -> Optional[float]:
        if self.width and self.height:
            return self.width / self.height
        return None


@dataclass
class ScoreResult:
    score: float                       # 0..100 combined
    per_signal: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    upgrade: Optional[MatchCategory] = None   # RESIZED_DUPLICATE / CROPPED_SIMILAR


def _norm_weights(weights: Optional[dict[str, float]]) -> dict[str, float]:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        for k in w:
            if k in weights and weights[k] is not None:
                w[k] = max(0.0, float(weights[k]))
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}


def _aspect_close(a: Optional[float], b: Optional[float], tol: float = 0.04) -> bool:
    if not a or not b:
        return False
    return abs(a - b) <= tol * max(a, b)


def combined_score(
    a: Signals,
    b: Signals,
    *,
    weights: Optional[dict[str, float]] = None,
    detect_resized: bool = True,
) -> ScoreResult:
    w = _norm_weights(weights)
    per: dict[str, float] = {}

    if a.phash is not None and b.phash is not None:
        per["phash"] = similarity_percent(hamming(a.phash, b.phash), HASH_BITS)
    if a.dhash is not None and b.dhash is not None:
        per["dhash"] = similarity_percent(hamming(a.dhash, b.dhash), HASH_BITS)
    if a.ahash is not None and b.ahash is not None:
        per["ahash"] = similarity_percent(hamming(a.ahash, b.ahash), HASH_BITS)
    if a.bhash is not None and b.bhash is not None:
        per["bhash"] = similarity_percent(hamming(a.bhash, b.bhash), BHASH_BITS)
    color = histogram_similarity(a.color_sig, b.color_sig)
    if color is not None:
        per["color"] = color

    if not per:
        return ScoreResult(score=0.0)

    # weighted average over the signals we actually have
    active = sum(w[k] for k in per)
    if active <= 0:
        score = round(sum(per.values()) / len(per), 1)
    else:
        score = round(sum(per[k] * w[k] for k in per) / active, 1)

    reasons: list[str] = []
    for key, val in per.items():
        if val >= _REASON_CUTS[key]:
            reasons.append(_REASON_TEXT[key])
    if _aspect_close(a.aspect, b.aspect):
        reasons.append("misma relación de aspecto")
    elif a.aspect and b.aspect:
        reasons.append("distinta relación de aspecto")
    if "color" in per and per["color"] < 55:
        reasons.append("colores claramente distintos")

    result = ScoreResult(score=score, per_signal=per, reasons=reasons)

    # RESIZED: same picture, different resolution
    if (
        detect_resized
        and score >= 96.0
        and _aspect_close(a.aspect, b.aspect)
        and a.width and b.width
        and abs(a.width - b.width) / max(a.width, b.width) >= 0.10
    ):
        result.upgrade = MatchCategory.RESIZED_DUPLICATE
        wa, ha, wb, hb = a.width, a.height, b.width, b.height
        result.reasons = [
            f"misma imagen a distinta resolución ({wa}×{ha} vs {wb}×{hb})",
            "diferencia de compresión / formato",
        ]

    return result


def classify_combined(score: float, bands: "SimilarityBands") -> MatchCategory:
    """Map a combined score to the base 'similar' family category."""
    if score >= bands.visually_identical:
        return MatchCategory.VISUALLY_IDENTICAL
    if score >= bands.very_similar:
        return MatchCategory.VERY_SIMILAR
    if score >= bands.similar:
        return MatchCategory.SIMILAR
    return MatchCategory.DIFFERENT


@dataclass
class SimilarityBands:
    visually_identical: float = 98.0
    very_similar: float = 90.0
    similar: float = 75.0

    def clamped(self) -> "SimilarityBands":
        vi = min(100.0, max(1.0, self.visually_identical))
        vs = min(vi, max(1.0, self.very_similar))
        si = min(vs, max(1.0, self.similar))
        return SimilarityBands(vi, vs, si)

    @classmethod
    def from_config(cls, config) -> "SimilarityBands":
        return resolve_bands(config)


# ---------------------------------------------------------------------------
# Sensitivity presets. "custom" reads the explicit values from the config.
_PRESETS: dict[str, dict] = {
    "low": {
        "bands": SimilarityBands(99.0, 96.0, 92.0),
        "detect_crops": False,
        "detect_resized": True,
        "dhash_confirm": 6,
    },
    "medium": {
        "bands": SimilarityBands(98.0, 92.0, 84.0),
        "detect_crops": False,
        "detect_resized": True,
        "dhash_confirm": 10,
    },
    "high": {
        "bands": SimilarityBands(97.0, 88.0, 75.0),
        "detect_crops": True,
        "detect_resized": True,
        "dhash_confirm": 16,
    },
}


@dataclass
class SensitivityProfile:
    bands: SimilarityBands
    weights: dict[str, float]
    detect_crops: bool
    detect_resized: bool
    dhash_confirm: int


def resolve_profile(config) -> SensitivityProfile:
    name = getattr(config, "sensitivity", "medium")
    if name == "custom":
        raw = getattr(config, "similarity_bands", None) or {}
        bands = SimilarityBands(
            float(raw.get("visually_identical", 98.0)),
            float(raw.get("very_similar", 90.0)),
            float(raw.get("similar", getattr(config, "similarity_threshold", 75))),
        ).clamped()
        return SensitivityProfile(
            bands=bands,
            weights=getattr(config, "similarity_weights", None) or dict(DEFAULT_WEIGHTS),
            detect_crops=bool(getattr(config, "detect_crops", False)),
            detect_resized=bool(getattr(config, "detect_resized", True)),
            dhash_confirm=int(getattr(config, "dhash_confirm_slack", 12)),
        )
    preset = _PRESETS.get(name, _PRESETS["medium"])
    return SensitivityProfile(
        bands=preset["bands"].clamped(),
        weights=dict(DEFAULT_WEIGHTS),
        detect_crops=preset["detect_crops"],
        detect_resized=preset["detect_resized"],
        dhash_confirm=preset["dhash_confirm"],
    )


def resolve_bands(config) -> SimilarityBands:
    return resolve_profile(config).bands
