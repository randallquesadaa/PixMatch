"""Records, per-file decisions and grouping of duplicates.

Grouping happens in layers, strongest evidence first, and a file lands in **at
most one** group:

  1. pixel digest equal  -> FILE_IDENTICAL  (if the bytes also match)
                          -> PIXEL_IDENTICAL (bytes differ, decoded pixels equal)
  2. SHA-256 equal        -> FILE_IDENTICAL  (fallback for images that failed to
                             decode, and for non-image files)
  3. perceptual hash near -> VISUALLY_IDENTICAL / VERY_SIMILAR / SIMILAR
                             (only when the user enabled "similar images")

The user's decision is stored *only in memory*. Nothing is ever deleted,
moved or renamed here - marking a file "for deletion" only records intent.
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

from app.core.metadata import ImageInfo, read_image_info
from app.core.perceptual import BKTree
from app.core.scanner import FileKind
from app.core.similarity import MatchCategory, distance_cutoff


class Decision(str, Enum):
    UNDECIDED = "undecided"
    KEEP = "keep"
    DELETE = "delete"  # marked for deletion only - never executed here


@dataclass
class FileRecord:
    path: str
    size: int
    mtime: float
    kind: FileKind
    sha256: str | None = None
    error: str | None = None

    # phase 2 analysis data
    pixel_digest: str | None = None
    phash: int | None = None
    dhash: int | None = None
    ahash: int | None = None
    bhash: int | None = None  # phase 6 - 256-bit layout hash
    color_sig: str | None = None  # phase 6 - HS histogram
    embedding: list | None = None  # phase 6 - optional CLIP-style vector
    width: int | None = None
    height: int | None = None

    # phase 4 (video) analysis data
    video_info: object | None = None  # core.video_analyzer.VideoInfo
    frame_hashes: list | None = None  # list[Optional[int]]

    # set when the record is part of a "similar" group: how close it is to the
    # group's anchor file, as a percentage.
    similarity_percent: float | None = None

    # UI state
    decision: Decision = Decision.UNDECIDED
    reviewed: bool = False
    deleted: bool = False  # set once the file was trashed / removed

    _image_info: ImageInfo | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def resolution(self) -> tuple[int, int] | None:
        if self.width and self.height:
            return self.width, self.height
        info = self.image_info()
        if not info.error:
            return info.oriented_size
        return None

    def image_info(self, refresh: bool = False) -> ImageInfo:
        if self._image_info is None or refresh:
            if self.kind is FileKind.IMAGE:
                self._image_info = read_image_info(self.path)
            else:
                self._image_info = ImageInfo(error="No es una imagen.")
        return self._image_info


@dataclass
class DuplicateGroup:
    group_id: int
    category: MatchCategory
    records: list[FileRecord]
    ignored: bool = False
    similarity_percent: float | None = None  # representative, for "similar" groups
    match_reasons: list[str] = field(default_factory=list)  # "por qué se agruparon"

    @property
    def confidence(self) -> int:
        from app.core.similarity import category_confidence

        return category_confidence(self.category, self.similarity_percent)

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def active_records(self) -> list[FileRecord]:
        """Records that still exist (not deleted this session)."""
        return [r for r in self.records if not r.deleted]

    @property
    def deleted_records(self) -> list[FileRecord]:
        return [r for r in self.records if r.deleted]

    @property
    def is_resolved(self) -> bool:
        """Nothing left to decide: ignored, or fewer than 2 files remain."""
        return self.ignored or len(self.active_records) < 2

    @property
    def total_size(self) -> int:
        return sum(r.size for r in self.records)

    @property
    def unit_size(self) -> int:
        """Size of one representative copy (the smallest, to stay conservative
        about how much space a group really represents)."""
        return min((r.size for r in self.records), default=0)

    @property
    def is_exact(self) -> bool:
        return self.category in (MatchCategory.FILE_IDENTICAL, MatchCategory.PIXEL_IDENTICAL)

    @property
    def potential_reclaimable(self) -> int:
        """If the user kept only the largest remaining file and removed the rest."""
        active = self.active_records
        if len(active) < 2:
            return 0
        return sum(r.size for r in active) - max(r.size for r in active)

    @property
    def marked_for_deletion(self) -> list[FileRecord]:
        return [r for r in self.records if r.decision is Decision.DELETE]

    @property
    def reclaimable_bytes(self) -> int:
        """Bytes freed by the *current decisions* (only DELETE-marked files)."""
        return sum(r.size for r in self.marked_for_deletion)

    @property
    def is_reviewed(self) -> bool:
        return (
            self.ignored
            or all(r.reviewed for r in self.records)
            or any(r.decision is not Decision.UNDECIDED for r in self.records)
        )

    @property
    def has_unsafe_decision(self) -> bool:
        return bool(self.records) and all(r.decision is Decision.DELETE for r in self.records)


# ---------------------------------------------------------------------------
def _sort_members(members: list[FileRecord]) -> list[FileRecord]:
    members.sort(key=lambda r: (r.mtime, r.path.lower()))
    return members


def _assign_ids(groups: list[DuplicateGroup]) -> list[DuplicateGroup]:
    groups.sort(
        key=lambda g: (g.category.rank, g.potential_reclaimable),
        reverse=True,
    )
    for index, group in enumerate(groups, start=1):
        group.group_id = index
    return groups


def build_exact_groups(records: Iterable[FileRecord]) -> list[DuplicateGroup]:
    """SHA-256 only grouping (kept for the phase-1 code path and tests)."""
    by_hash: dict[str, list[FileRecord]] = defaultdict(list)
    for rec in records:
        if rec.sha256:
            by_hash[rec.sha256].append(rec)

    groups: list[DuplicateGroup] = []
    for members in by_hash.values():
        if len(members) < 2:
            continue
        groups.append(DuplicateGroup(0, MatchCategory.FILE_IDENTICAL, _sort_members(members)))
    groups.sort(key=lambda g: (g.count - 1) * g.unit_size, reverse=True)
    for index, group in enumerate(groups, start=1):
        group.group_id = index
    return groups


def build_groups(
    records: Iterable[FileRecord],
    *,
    profile=None,
    enable_perceptual: bool = False,
    enable_video: bool = False,
    crop_edges: list | None = None,
) -> list[DuplicateGroup]:
    """Layered grouping. ``profile`` is a
    :class:`app.core.similarity_engine.SensitivityProfile` (default = medium).
    ``crop_edges`` is a list of ``(rec_a, rec_b, score, region)`` produced by
    the pipeline's crop-detection phase."""
    from app.core.similarity_engine import SensitivityProfile, SimilarityBands

    if profile is None:
        profile = SensitivityProfile(
            bands=SimilarityBands().clamped(),
            weights=None,
            detect_crops=False,
            detect_resized=True,
            dhash_confirm=10,
        )

    records = list(records)
    groups: list[DuplicateGroup] = []
    assigned: set[int] = set()

    # -- layer 1: exact decoded pixels -------------------------------
    by_pixels: dict[str, list[FileRecord]] = defaultdict(list)
    for rec in records:
        if rec.pixel_digest:
            by_pixels[rec.pixel_digest].append(rec)
    for members in by_pixels.values():
        if len(members) < 2:
            continue
        shas = {m.sha256 for m in members}
        if len(shas) == 1 and None not in shas:
            category = MatchCategory.FILE_IDENTICAL
        else:
            category = MatchCategory.PIXEL_IDENTICAL
        groups.append(DuplicateGroup(0, category, _sort_members(members)))
        assigned.update(id(m) for m in members)

    # -- layer 2: SHA-256 (files with no usable pixel digest) --------
    # When video analysis is on, videos are grouped entirely in layer 4 (which
    # merges byte-identical + re-encoded into one group), so skip them here.
    by_hash: dict[str, list[FileRecord]] = defaultdict(list)
    for rec in records:
        if id(rec) in assigned or not rec.sha256:
            continue
        if enable_video and rec.kind is FileKind.VIDEO:
            continue
        by_hash[rec.sha256].append(rec)
    for members in by_hash.values():
        if len(members) < 2:
            continue
        groups.append(DuplicateGroup(0, MatchCategory.FILE_IDENTICAL, _sort_members(members)))
        assigned.update(id(m) for m in members)

    # -- layer 3: combined-signal similarity (images) ----------------
    if enable_perceptual:
        groups.extend(
            _similarity_groups(
                [
                    r
                    for r in records
                    if id(r) not in assigned and r.kind is FileKind.IMAGE and r.phash is not None
                ],
                profile,
                crop_edges or [],
            )
        )

    # -- layer 4: video (byte-identical + sampled-frame similarity) --
    if enable_video:
        vids = [r for r in records if id(r) not in assigned and r.kind is FileKind.VIDEO]
        groups.extend(_video_groups(vids, profile.bands.similar))

    return _assign_ids(groups)


def _video_groups(candidates: list[FileRecord], threshold: float) -> list[DuplicateGroup]:
    """Group videos by two kinds of evidence at once:

    * identical SHA-256  -> the files are byte-identical
    * close sampled frames -> probably the same content (re-encode, rename)

    Both go into a single group; its category reflects the *weakest* link
    between members (so a group with one re-encoded copy is "same content
    likely", never "identical").
    """
    from app.core.similarity import classify_video_similarity
    from app.core.video_analyzer import compare_frame_hashes

    if len(candidates) < 2:
        return []

    def duration(rec: FileRecord) -> float:
        info = rec.video_info
        return getattr(info, "duration", 0.0) if info is not None else 0.0

    def has_frames(rec: FileRecord) -> bool:
        return bool(rec.frame_hashes) and any(h is not None for h in rec.frame_hashes)

    parent: dict[int, int] = {id(r): id(r) for r in candidates}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # edges by identical bytes
    by_hash: dict[str, list[FileRecord]] = defaultdict(list)
    for rec in candidates:
        if rec.sha256:
            by_hash[rec.sha256].append(rec)
    byte_identical: set[frozenset[int]] = set()
    for members in by_hash.values():
        for other in members[1:]:
            union(id(members[0]), id(other))
        if len(members) > 1:
            byte_identical.add(frozenset(id(m) for m in members))

    # edges by frame similarity
    pair_pct: dict[frozenset[int], float] = {}
    frame_recs = [r for r in candidates if has_frames(r)]
    for i, a in enumerate(frame_recs):
        for b in frame_recs[i + 1 :]:
            if abs(duration(a) - duration(b)) > 2.0:
                continue
            pct = compare_frame_hashes(a.frame_hashes, b.frame_hashes)
            if pct is None or pct < threshold:
                continue
            pair_pct[frozenset((id(a), id(b)))] = pct
            union(id(a), id(b))

    clusters: dict[int, list[FileRecord]] = defaultdict(list)
    for rec in candidates:
        clusters[find(id(rec))].append(rec)

    groups: list[DuplicateGroup] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members = _sort_members(members)
        ids = [id(m) for m in members]
        anchor = max(members, key=lambda r: r.size)

        all_same_hash = len({m.sha256 for m in members}) == 1 and all(m.sha256 for m in members)
        if all_same_hash:
            category = MatchCategory.FILE_IDENTICAL
            rep = 100.0
        else:
            # weakest frame link that holds the cluster together
            pcts = [p for key, p in pair_pct.items() if key.issubset(set(ids))]
            rep = min(pcts) if pcts else float(threshold)
            durations = [duration(m) for m in members]
            same_duration = max(durations) - min(durations) <= 0.75
            category = classify_video_similarity(rep, threshold, same_duration)
            if category is MatchCategory.DIFFERENT:
                continue

        for rec in members:
            if rec is anchor or (rec.sha256 and rec.sha256 == anchor.sha256):
                rec.similarity_percent = 100.0
            elif has_frames(rec) and has_frames(anchor):
                pct = compare_frame_hashes(rec.frame_hashes, anchor.frame_hashes)
                rec.similarity_percent = pct if pct is not None else rep
            else:
                rec.similarity_percent = rep

        groups.append(
            DuplicateGroup(
                0,
                category,
                members,
                similarity_percent=None if category is MatchCategory.FILE_IDENTICAL else rep,
            )
        )
    return groups


def _signals(rec: FileRecord):
    from app.core.similarity_engine import Signals

    return Signals(
        phash=rec.phash,
        dhash=rec.dhash,
        ahash=rec.ahash,
        bhash=rec.bhash,
        color_sig=rec.color_sig,
        width=rec.width,
        height=rec.height,
    )


def _similarity_groups(
    candidates: list[FileRecord],
    profile,
    crop_edges: list,
) -> list[DuplicateGroup]:
    """Group images using the combined-signal engine, plus dedicated
    RESIZED / CROPPED categories, and record *why* each group was formed."""
    from app.core.similarity_engine import classify_combined, combined_score

    groups: list[DuplicateGroup] = []
    bands = profile.bands
    weights = profile.weights

    if len(candidates) < 2 and not crop_edges:
        return groups

    # ---- perceptual clustering via combined score ----
    cutoff = max(8, min(30, distance_cutoff(bands.similar) + 10))
    tree = BKTree()
    for rec in candidates:
        tree.add(rec.phash, rec)

    parent: dict[int, int] = {id(r): id(r) for r in candidates}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    edges: dict[frozenset, object] = {}
    seen: set[frozenset] = set()
    for rec in candidates:
        for other in tree.query(rec.phash, cutoff):
            if other is rec:
                continue
            key = frozenset((id(rec), id(other)))
            if key in seen:
                continue
            seen.add(key)
            res = combined_score(
                _signals(rec),
                _signals(other),
                weights=weights,
                detect_resized=profile.detect_resized,
            )
            # anti-false-positive: composition must not be wildly different
            if res.per_signal.get("bhash", 100.0) < 55.0:
                continue

            # optional AI embedding: a strong "different scene" vote overrides a
            # lucky hash match; a strong "same scene" vote rescues a borderline.
            emb = None
            if rec.embedding and other.embedding:
                from app.core.embeddings import similarity_percent as _emb_sim

                emb = _emb_sim(rec.embedding, other.embedding)
                if emb is not None:
                    if emb < 55.0:
                        continue
                    res.reasons.append(f"IA: misma escena ({emb:.0f}%)")
                    if res.score < bands.similar and emb >= 90.0:
                        res.score = max(res.score, bands.similar)

            if res.score < bands.similar:
                continue
            edges[key] = res
            union(id(rec), id(other))

    clusters: dict[int, list[FileRecord]] = defaultdict(list)
    for rec in candidates:
        clusters[find(id(rec))].append(rec)

    for members in clusters.values():
        if len(members) < 2:
            continue
        members = _sort_members(members)
        ids = {id(m) for m in members}
        anchor = max(members, key=lambda r: (r.size, (r.width or 0) * (r.height or 0)))

        headline = 0.0
        weakest = 100.0
        for rec in members:
            if rec is anchor:
                rec.similarity_percent = 100.0
                continue
            res = combined_score(
                _signals(rec),
                _signals(anchor),
                weights=weights,
                detect_resized=profile.detect_resized,
            )
            rec.similarity_percent = res.score
            headline = max(headline, res.score)
            weakest = min(weakest, res.score)

        cluster_edges = [r for k, r in edges.items() if k <= ids]
        has_resize_edge = any(e.upgrade is MatchCategory.RESIZED_DUPLICATE for e in cluster_edges)
        distinct_res = len({(m.width, m.height) for m in members if m.width}) > 1

        # RESIZED_DUPLICATE only when resolution is the *only* difference:
        # multiple resolutions, and even the weakest pair is "visually identical".
        if has_resize_edge and distinct_res and weakest >= bands.visually_identical:
            category = MatchCategory.RESIZED_DUPLICATE
        else:
            category = classify_combined(headline or bands.visually_identical, bands)
        if category is MatchCategory.DIFFERENT:
            continue

        reasons: list[str] = []
        for e in cluster_edges:
            for r in e.reasons:
                if r not in reasons:
                    reasons.append(r)
        if category is MatchCategory.RESIZED_DUPLICATE and not any(
            "resoluci" in r for r in reasons
        ):
            reasons.insert(0, "misma imagen a distinta resolución")
        groups.append(
            DuplicateGroup(
                0,
                category,
                members,
                similarity_percent=round(headline, 1) if headline else None,
                match_reasons=reasons[:6],
            )
        )

    # ---- crop groups: run last, only for images not already grouped ----
    if crop_edges:
        perceptual_ids = {id(r) for g in groups for r in g.records}
        # group crops by their (larger) source image
        by_source: dict[int, list] = defaultdict(list)
        src_by_id: dict[int, FileRecord] = {}
        for a, b, score, region in crop_edges:
            small = a if (a.width or 0) * (a.height or 0) < (b.width or 0) * (b.height or 0) else b
            big = b if small is a else a
            if id(small) in perceptual_ids:
                continue  # the crop is already in a similarity group
            src_by_id[id(big)] = big
            by_source[id(big)].append((small, score, region))

        for big_id, crops in by_source.items():
            big = src_by_id[big_id]
            members = _sort_members([big] + [c[0] for c in crops])
            best = max(c[1] for c in crops)
            big.similarity_percent = 100.0
            for small, score, _ in crops:
                small.similarity_percent = score
            _, _, region = max(crops, key=lambda c: c[1])
            x0, y0, x1, y1 = region
            groups.append(
                DuplicateGroup(
                    0,
                    MatchCategory.CROPPED_SIMILAR,
                    members,
                    similarity_percent=round(best, 1),
                    match_reasons=[
                        "una imagen parece un recorte (encuadre parcial) de la otra",
                        f"región coincidente ≈ {int((x1 - x0) * 100)}% × {int((y1 - y0) * 100)}% del original",
                        "distinta relación de aspecto",
                    ],
                )
            )

    return groups


# ---------------------------------------------------------------------------
@dataclass
class AnalysisResult:
    root: str
    incremental: bool
    scan_stats: object  # ScanStats
    groups: list[DuplicateGroup]
    hashed_files: int
    elapsed_seconds: float
    cancelled: bool = False
    decoded_files: int = 0
    perceptual_files: int = 0
    cache_hits: int = 0
    videos_probed: int = 0
    video_frames_sampled: int = 0
    ffmpeg_available: bool = True
    crop_pairs_found: int = 0
    sensitivity: str = "medium"

    # ----- post-deletion maintenance -------------------------------
    def drop_deleted(self) -> int:
        """Remove records that were deleted; drop groups with < 2 survivors.
        Returns the number of files removed from the view."""
        removed = 0
        kept: list[DuplicateGroup] = []
        for group in self.groups:
            survivors = [r for r in group.records if not r.deleted]
            removed += len(group.records) - len(survivors)
            if len(survivors) >= 2:
                group.records = survivors
                kept.append(group)
        self.groups = kept
        return removed

    # ----- dashboard numbers -----------------------------------------
    def _groups_of(self, *categories: MatchCategory) -> list[DuplicateGroup]:
        return [g for g in self.groups if g.category in categories]

    @property
    def image_groups(self) -> list[DuplicateGroup]:
        return [g for g in self.groups if all(r.kind is FileKind.IMAGE for r in g.records)]

    @property
    def video_groups(self) -> list[DuplicateGroup]:
        return [g for g in self.groups if all(r.kind is FileKind.VIDEO for r in g.records)]

    @property
    def file_identical_groups(self) -> list[DuplicateGroup]:
        return self._groups_of(MatchCategory.FILE_IDENTICAL)

    @property
    def pixel_identical_groups(self) -> list[DuplicateGroup]:
        return self._groups_of(MatchCategory.PIXEL_IDENTICAL)

    @property
    def similar_groups(self) -> list[DuplicateGroup]:
        return self._groups_of(
            MatchCategory.VISUALLY_IDENTICAL,
            MatchCategory.SAME_CONTENT_LIKELY,
            MatchCategory.RESIZED_DUPLICATE,
            MatchCategory.CROPPED_SIMILAR,
            MatchCategory.VERY_SIMILAR,
            MatchCategory.SIMILAR,
        )

    @property
    def resized_groups(self) -> list[DuplicateGroup]:
        return self._groups_of(MatchCategory.RESIZED_DUPLICATE)

    @property
    def cropped_groups(self) -> list[DuplicateGroup]:
        return self._groups_of(MatchCategory.CROPPED_SIMILAR)

    @property
    def video_groups_all(self) -> list[DuplicateGroup]:
        return [
            g for g in self.groups if g.records and all(r.kind is FileKind.VIDEO for r in g.records)
        ]

    @property
    def identical_file_count(self) -> int:
        return sum(g.count for g in self.file_identical_groups)

    @property
    def pixel_identical_file_count(self) -> int:
        return sum(g.count for g in self.pixel_identical_groups)

    @property
    def similar_file_count(self) -> int:
        return sum(g.count for g in self.similar_groups)

    @property
    def unresolved_groups(self) -> list[DuplicateGroup]:
        return [g for g in self.groups if not g.is_resolved]

    @property
    def deleted_file_count(self) -> int:
        return sum(len(g.deleted_records) for g in self.groups)

    @property
    def redundant_copies(self) -> int:
        """Files that could still be removed keeping one per group."""
        return sum(max(0, len(g.active_records) - 1) for g in self.groups)

    @property
    def reclaimable_bytes(self) -> int:
        """Upper-bound estimate: keep the largest remaining file in each group."""
        return sum(g.potential_reclaimable for g in self.groups)
