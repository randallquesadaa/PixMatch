"""A *non-binding* suggestion of which file to keep in a group.

The recommendation is scored, never executed. Nothing is ever pre-selected for
deletion; the user is free to ignore this entirely.

KEEP_SCORE combines, roughly in order of weight:
  * resolution / pixel count      (a bigger image/video usually has more data)
  * file size                     (larger = less compressed, for the same content)
  * container / codec quality     (lossless > lossy; higher bitrate)
  * metadata richness             (EXIF, camera, capture date, creation time)
  * age                           (the oldest copy is likely the original)
  * location & name               (penalise "copy", backup / temp folders,
                                   deep or long paths)

For a byte-identical group every copy has the same content, so the score is
really about *where* to keep it - the confidence is reported lower unless
there is a clear signal (an obvious copy name, a backup folder).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.core.duplicate_groups import DuplicateGroup, FileRecord
from app.core.similarity import MatchCategory

_COPY_HINTS = (
    "copy",
    "copia",
    "copie",
    "kopie",
    "- copy",
    "(1)",
    "(2)",
    "(3)",
    "-copy",
    "_copy",
    " copy",
    "duplicate",
    "duplicado",
    "dup",
    " - copia",
    "conflicted",
    "conflicto",
)
_BAD_DIR_HINTS = (
    "backup",
    "back up",
    "respaldo",
    "copia de seguridad",
    "papelera",
    "trash",
    ".trash",
    "recycle",
    "$recycle",
    "tmp",
    "temp",
    "temporal",
    "cache",
    ".cache",
    "downloads",
    "descargas",
    "whatsapp",
    "telegram",
)
_LOSSLESS = {"PNG", "BMP", "TIFF", "TIF", "WEBP-LOSSLESS", "GIF"}
_LOSSY = {"JPEG", "JPG", "WEBP", "HEIC", "HEIF", "AVIF"}


@dataclass
class Recommendation:
    record: FileRecord
    confidence: int  # 0-100
    reasons: list[str]
    scores: dict = field(default_factory=dict)  # id(record) -> score (debug/UI)


def _pixels(rec: FileRecord) -> int:
    res = rec.resolution
    return res[0] * res[1] if res else 0


def _bitrate(rec: FileRecord) -> int:
    info = rec.video_info
    return getattr(info, "bitrate", 0) if info is not None else 0


def _format_rank(rec: FileRecord) -> int:
    if rec.kind.value != "image":
        return 0
    info = rec.image_info()
    if info.error:
        return 0
    fmt = (info.format or "").upper()
    if fmt in _LOSSLESS:
        return 2
    if fmt in _LOSSY:
        return 1
    return 0


def _metadata_points(rec: FileRecord) -> int:
    if rec.kind.value == "image":
        info = rec.image_info()
        if info.error:
            return 0
        pts = 0
        if info.has_exif:
            pts += 1
        if info.camera_make or info.camera_model:
            pts += 1
        if info.date_taken:
            pts += 1
        return pts
    if rec.kind.value == "video":
        info = rec.video_info
        return 1 if info is not None and getattr(info, "creation_time", None) else 0
    return 0


def _looks_like_a_copy(name: str) -> bool:
    low = name.lower()
    return any(hint in low for hint in _COPY_HINTS)


def _in_bad_folder(path: str) -> bool:
    parent = os.path.dirname(path).lower()
    return any(hint in parent for hint in _BAD_DIR_HINTS)


def recommend_keep(group: DuplicateGroup) -> Recommendation | None:
    records = group.active_records
    if len(records) < 2:
        return None

    max_px = max((_pixels(r) for r in records), default=0)
    max_size = max(r.size for r in records)
    max_bitrate = max((_bitrate(r) for r in records), default=0)
    max_meta = max((_metadata_points(r) for r in records), default=0)
    oldest = min(r.mtime for r in records)
    shortest_path = min(len(r.path) for r in records)
    min_depth = min(r.path.count(os.sep) for r in records)
    is_exact = group.category is MatchCategory.FILE_IDENTICAL

    scored: list[tuple[float, list[str], FileRecord]] = []
    for rec in records:
        score = 0.0
        reasons: list[str] = []

        px = _pixels(rec)
        if max_px > 0 and not is_exact:
            score += 30.0 * px / max_px
            if px == max_px and any(_pixels(o) < max_px for o in records):
                reasons.append("mayor resolución")

        if rec.size == max_size and any(o.size < max_size for o in records):
            score += 12.0
            if not is_exact:
                reasons.append("archivo más grande (menos comprimido)")

        fr = _format_rank(rec)
        score += fr * 5.0
        if fr == 2 and any(_format_rank(o) < 2 for o in records) and not is_exact:
            reasons.append("formato sin pérdidas")

        br = _bitrate(rec)
        if max_bitrate > 0:
            score += 10.0 * br / max_bitrate
            if br == max_bitrate and any(_bitrate(o) < max_bitrate for o in records):
                reasons.append("mayor bitrate de vídeo")

        mp = _metadata_points(rec)
        score += mp * 4.0
        if mp == max_meta and max_meta > 0 and any(_metadata_points(o) < max_meta for o in records):
            reasons.append("conserva más metadatos (EXIF / cámara / fecha)")

        if rec.mtime <= oldest + 2.0:
            score += 9.0
            reasons.append("el archivo más antiguo (probable original)")

        if _looks_like_a_copy(rec.name):
            score -= 14.0
            reasons.append("el nombre indica que es una copia")
        else:
            score += 6.0

        if _in_bad_folder(rec.path):
            score -= 10.0
            reasons.append("está en una carpeta de backup / temporal / descargas")

        depth = rec.path.count(os.sep)
        if depth == min_depth:
            score += 3.0
        if len(rec.path) <= shortest_path + 4:
            score += 2.0
            if not reasons:
                reasons.append("ruta más corta / directa")

        scored.append((score, reasons, rec))

    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best_reasons, best = scored[0]
    runner_up_score = scored[1][0]
    margin = best_score - runner_up_score

    # confidence: driven by the margin, capped lower for exact groups without
    # a decisive reason
    confidence = int(max(35, min(96, 52 + margin * 2.4)))
    decisive = any(
        kw in " ".join(best_reasons)
        for kw in ("copia", "backup", "resolución", "sin pérdidas", "metadatos")
    )
    if is_exact and not decisive:
        confidence = min(confidence, 55)
        if not best_reasons:
            best_reasons = ["sin diferencias de contenido; se sugiere por ubicación y antigüedad"]

    return Recommendation(
        record=best,
        confidence=confidence,
        reasons=best_reasons[:4] or ["sin señales claras; cualquiera sirve"],
        scores={id(r): round(s, 1) for s, _, r in scored},
    )
