"""Analysis pipeline (pure Python, no Qt).

    scan
      -> [cache lookup]
      -> SHA-256            (size-collision candidates)        -> FILE_IDENTICAL
      -> metadata + pixels  (dimension-collision candidates)   -> PIXEL_IDENTICAL
      -> perceptual hashes  (opt-in, all images)               -> SIMILAR / ...
      -> group
      -> [cache write]

Every expensive step is cache-aware: on an incremental run a file whose size
and mtime are unchanged reuses the stored hashes instead of being decoded
again.

Progress and lifecycle are reported through :class:`AnalysisCallbacks` so the
module stays independent of the GUI.
"""
from __future__ import annotations

import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from app.config import AppConfig
from app.core.duplicate_groups import AnalysisResult, FileRecord, build_groups
from app.core.hashing import HashCancelled, hash_file
from app.core.image_analyzer import pixel_digest as compute_pixel_digest
from app.core.image_features import compute_signatures
from app.core.metadata import read_image_info
from app.core.scanner import FileKind, ScannedFile, Scanner, ScanOptions, ScanStats
from app.utils.control import RunController
from app.utils.logging_setup import get_logger

log = get_logger(__name__)

Phase = str  # scanning | hashing | pixels | perceptual | grouping | done


@dataclass
class StepProgress:
    label: str
    done: int
    total: int
    groups_so_far: int
    elapsed: float

    @property
    def eta_seconds(self) -> float:
        if self.done <= 0:
            return 0.0
        rate = self.done / max(self.elapsed, 1e-6)
        remaining = max(0, self.total - self.done)
        return remaining / rate if rate else 0.0


class AnalysisCallbacks:
    """Override what you need. All methods are called from a worker thread."""

    def on_phase(self, phase: Phase) -> None: ...
    def on_scan_progress(self, stats: ScanStats) -> None: ...
    def on_step_progress(self, progress: StepProgress) -> None: ...
    def on_message(self, text: str) -> None: ...


def _include_kinds(config: AppConfig) -> frozenset[FileKind]:
    kinds = set()
    if config.analyze_images:
        kinds.add(FileKind.IMAGE)
    if config.analyze_videos:
        kinds.add(FileKind.VIDEO)
    return frozenset(kinds or {FileKind.IMAGE})


# ---------------------------------------------------------------------------
def _parallel(
    items: list,
    fn: Callable,
    *,
    workers: int,
    controller: RunController,
    handle: Callable,
    emit: Callable[[StepProgress], None],
    label: str,
    started: float,
    group_count: Optional[Callable[[], int]] = None,
) -> None:
    total = len(items)
    if total == 0:
        return
    workers = max(1, int(workers))
    done = 0
    last = 0.0

    def push_progress(force: bool = False) -> None:
        nonlocal last
        now = time.monotonic()
        if force or now - last > 0.15:
            last = now
            emit(StepProgress(
                label, done, total,
                group_count() if group_count else 0,
                now - started,
            ))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: dict = {}
        it = iter(items)

        def fill() -> bool:
            try:
                item = next(it)
            except StopIteration:
                return False
            pending[pool.submit(fn, item)] = item
            return True

        for _ in range(min(workers * 4, total)):
            fill()

        while pending:
            if not controller.checkpoint():   # blocks while paused, False on cancel
                break
            finished, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for fut in finished:
                item = pending.pop(fut)
                exc = fut.exception()
                result = None if exc else fut.result()
                handle(item, result, exc)
                done += 1
                if not controller.is_cancelled:
                    fill()
                push_progress()

        if controller.is_cancelled:
            for fut in pending:
                fut.cancel()

    push_progress(force=True)


# ---------------------------------------------------------------------------
def run_analysis(
    root: str,
    config: AppConfig,
    controller: Optional[RunController] = None,
    callbacks: Optional[AnalysisCallbacks] = None,
    incremental: bool = False,
    db_path: Optional[str] = None,
) -> AnalysisResult:
    controller = controller or RunController()
    cb = callbacks or AnalysisCallbacks()
    started = time.monotonic()

    # -- 1. scan ---------------------------------------------------
    cb.on_phase("scanning")
    scan_opts = ScanOptions(
        follow_symlinks=config.follow_symlinks,
        min_size_bytes=config.min_size_bytes,
        ignored_extensions=ScanOptions.normalise_ext(config.ignored_extensions),
        excluded_dir_names=frozenset(config.excluded_dir_names),
        excluded_paths=frozenset(config.excluded_paths),
        include_kinds=_include_kinds(config),
    )
    collected: list[ScannedFile] = []
    scan_stats = Scanner(root, scan_opts, controller).scan(
        on_file=collected.append,
        on_progress=cb.on_scan_progress,
    )
    for issue in scan_stats.issues:
        log.warning("Scan issue: %s -> %s", issue.path, issue.message)

    if controller.is_cancelled:
        return AnalysisResult(
            root=root, incremental=incremental, scan_stats=scan_stats,
            groups=[], hashed_files=0,
            elapsed_seconds=time.monotonic() - started, cancelled=True,
        )

    # -- 2. cache lookup -------------------------------------------
    db = _open_cache(config, db_path)
    cached = db.get_many(f.path for f in collected) if db else {}

    records: dict[str, FileRecord] = {}
    cache_hits = 0
    for sf in collected:
        rec = FileRecord(path=sf.path, size=sf.size, mtime=sf.mtime, kind=sf.kind)
        row = cached.get(sf.path)
        if incremental and row and row.is_valid_for(sf.size, sf.mtime):
            cache_hits += 1
            rec.sha256 = row.sha256
            rec.pixel_digest = row.pixel_digest
            rec.phash = _hex_to_int(row.phash)
            rec.dhash = _hex_to_int(row.dhash)
            rec.ahash = _hex_to_int(row.ahash)
            rec.bhash = _hex_to_int(row.bhash)
            rec.color_sig = row.color_sig or None
            if row.embedding:
                from app.core.embeddings import decode as _emb_decode

                rec.embedding = _emb_decode(row.embedding)
            rec.width, rec.height = row.width, row.height
            rec.error = row.decode_error
        records[sf.path] = rec

    images = [sf for sf in collected if sf.kind is FileKind.IMAGE]

    # -- 3. SHA-256 ----------------------------------------------
    cb.on_phase("hashing")
    by_size: dict[int, list[ScannedFile]] = defaultdict(list)
    for sf in collected:
        by_size[sf.size].append(sf)
    sha_candidates = [
        sf for group in by_size.values() if len(group) > 1 for sf in group
        if records[sf.path].sha256 is None
    ]
    cb.on_message(
        f"{len(collected)} archivos; {len(sha_candidates)} comparten tamaño "
        f"y se verifican con SHA-256"
        + (f"; {cache_hits} reutilizados de la caché" if cache_hits else "")
    )

    def _hash(sf: ScannedFile) -> str:
        return hash_file(sf.path, check=controller.checkpoint)

    def _hash_done(sf: ScannedFile, result, exc) -> None:
        rec = records[sf.path]
        if exc is None:
            rec.sha256 = result
        elif isinstance(exc, HashCancelled):
            pass
        else:
            rec.error = f"No se pudo leer para hashing: {exc}"
            log.warning("Hash failed: %s -> %s", sf.path, exc)

    _parallel(
        sha_candidates, _hash,
        workers=config.workers, controller=controller,
        handle=_hash_done, emit=cb.on_step_progress,
        label="SHA-256", started=started,
        group_count=lambda: _rough_hash_groups(records.values()),
    )

    decoded_files = 0
    perceptual_files = 0

    # -- 4. metadata + pixel digest -----------------------------
    if config.detect_pixel_identical and not controller.is_cancelled:
        cb.on_phase("pixels")
        meta_needed = [
            sf for sf in images
            if records[sf.path].width is None and records[sf.path].error is None
        ]

        def _meta(sf: ScannedFile):
            return read_image_info(sf.path)

        def _meta_done(sf: ScannedFile, info, exc) -> None:
            rec = records[sf.path]
            if exc is not None:
                rec.error = f"No se pudo leer metadatos: {exc}"
                return
            rec._image_info = info
            if info.error:
                rec.error = info.error
            else:
                rec.width, rec.height = info.oriented_size

        _parallel(
            meta_needed, _meta,
            workers=config.workers, controller=controller,
            handle=_meta_done, emit=cb.on_step_progress,
            label="Metadatos", started=started,
        )

        by_dims: dict[tuple[int, int], list[ScannedFile]] = defaultdict(list)
        for sf in images:
            rec = records[sf.path]
            if rec.width and rec.height:
                by_dims[(rec.width, rec.height)].append(sf)
        pixel_candidates = [
            sf for group in by_dims.values() if len(group) > 1 for sf in group
            if records[sf.path].pixel_digest is None and records[sf.path].error is None
        ]
        decoded_files = len(pixel_candidates)

        def _pixels(sf: ScannedFile):
            return compute_pixel_digest(sf.path)

        def _pixels_done(sf: ScannedFile, result, exc) -> None:
            rec = records[sf.path]
            if exc is not None or result is None:
                if exc is not None:
                    rec.error = f"No se pudo decodificar: {exc}"
                return
            rec.pixel_digest, info = result
            rec.width, rec.height = info.width, info.height

        _parallel(
            pixel_candidates, _pixels,
            workers=config.workers, controller=controller,
            handle=_pixels_done, emit=cb.on_step_progress,
            label="Comparación de píxeles", started=started,
            group_count=lambda: _rough_pixel_groups(records.values()),
        )

    # -- 5. image signatures for the similarity engine (opt-in) ----
    from app.core.similarity_engine import resolve_profile

    profile = resolve_profile(config)
    crop_edges: list = []
    if config.analyze_similar_images and not controller.is_cancelled:
        cb.on_phase("perceptual")
        sig_needed = [
            sf for sf in images
            if records[sf.path].phash is None and records[sf.path].error is None
        ]
        perceptual_files = len(sig_needed)

        def _sigs(sf: ScannedFile):
            return compute_signatures(sf.path)

        def _sigs_done(sf: ScannedFile, result, exc) -> None:
            rec = records[sf.path]
            if exc is not None or result is None:
                return
            rec.phash, rec.dhash, rec.ahash, rec.bhash = (
                result.phash, result.dhash, result.ahash, result.bhash
            )
            rec.color_sig = result.color_sig
            if not rec.width:
                rec.width, rec.height = result.width, result.height

        _parallel(
            sig_needed, _sigs,
            workers=config.workers, controller=controller,
            handle=_sigs_done, emit=cb.on_step_progress,
            label="Firmas de imagen", started=started,
        )

        # -- 5b. optional AI embeddings ---------------------------
        if config.use_ai_embeddings and not controller.is_cancelled:
            from app.core import embeddings as _emb

            if _emb.available():
                emb_needed = [
                    sf for sf in images
                    if records[sf.path].embedding is None
                    and records[sf.path].error is None
                ]
                cb.on_phase("embeddings")

                def _do_emb(sf: ScannedFile):
                    return _emb.embed(sf.path)

                def _emb_done(sf: ScannedFile, result, exc) -> None:
                    if exc is None and result:
                        records[sf.path].embedding = result

                _parallel(
                    emb_needed, _do_emb,
                    workers=max(1, min(config.workers, 2)), controller=controller,
                    handle=_emb_done, emit=cb.on_step_progress,
                    label="Embeddings visuales", started=started,
                )
            else:
                cb.on_message(
                    "«Detección avanzada mediante IA» activada pero sin backend "
                    "instalado: se usan solo los métodos tradicionales."
                )

        # -- 5c. crop detection (high sensitivity only) ------------
        if profile.detect_crops and not controller.is_cancelled:
            crop_edges = _detect_crops(
                [records[sf.path] for sf in images],
                controller, cb, config.workers, started, profile.bands,
            )

    # -- 6. video analysis (opt-in, needs FFmpeg) -----------------
    videos_probed = 0
    video_frames_sampled = 0
    ffmpeg_available = True
    if config.analyze_videos and not controller.is_cancelled:
        from app.core.ffmpeg import detect as _detect_ffmpeg
        from app.core.video_analyzer import (
            VideoInfo,
            decode_frame_hashes,
            extract_frame_hashes,
            probe_video,
        )

        tools = _detect_ffmpeg(config.ffmpeg_path, config.ffprobe_path)
        ffmpeg_available = tools.available
        video_files = [sf for sf in collected if sf.kind is FileKind.VIDEO]

        # restore cached video data
        for sf in video_files:
            rec = records[sf.path]
            row = cached.get(sf.path)
            if incremental and row and row.is_valid_for(sf.size, sf.mtime) and row.video_duration:
                rec.video_info = VideoInfo(
                    duration=row.video_duration or 0.0,
                    width=row.video_width or 0, height=row.video_height or 0,
                    video_codec=row.video_codec or "", fps=row.video_fps or 0.0,
                    bitrate=row.video_bitrate or 0,
                    has_audio=bool(row.video_audio), audio_codec=row.video_audio or "",
                )
                rec.frame_hashes = decode_frame_hashes(row.frame_hashes) or None

        if not tools.available:
            from app.core.ffmpeg import install_hint

            cb.on_message(
                "FFmpeg no está disponible: se omite el análisis de vídeo. "
                + install_hint()
            )
        elif video_files:
            cb.on_phase("video_metadata")
            probe_needed = [
                sf for sf in video_files
                if records[sf.path].video_info is None
                and records[sf.path].error is None
            ]

            def _probe(sf: ScannedFile):
                return probe_video(sf.path, tools)

            def _probe_done(sf: ScannedFile, info, exc) -> None:
                rec = records[sf.path]
                if exc is not None:
                    rec.error = f"No se pudo analizar el vídeo: {exc}"
                    return
                rec.video_info = info
                if info.error:
                    rec.error = info.error
                elif info.width and info.height:
                    rec.width, rec.height = info.width, info.height

            _parallel(
                probe_needed, _probe,
                workers=max(1, min(config.workers, 4)), controller=controller,
                handle=_probe_done, emit=cb.on_step_progress,
                label="Metadatos de vídeo", started=started,
            )
            videos_probed = sum(
                1 for sf in video_files
                if getattr(records[sf.path].video_info, "ok", False)
            )

            # frame-hash candidates: another video within 2s of duration and not
            # already byte-identical
            def _dur(sf: ScannedFile) -> float:
                info = records[sf.path].video_info
                return getattr(info, "duration", 0.0) if info else 0.0

            probed = [sf for sf in video_files if getattr(records[sf.path].video_info, "ok", False)]
            frame_candidates = []
            for sf in probed:
                if records[sf.path].frame_hashes is not None:
                    continue
                if any(
                    other is not sf and abs(_dur(sf) - _dur(other)) <= 2.0
                    for other in probed
                ):
                    frame_candidates.append(sf)

            if frame_candidates and not controller.is_cancelled:
                cb.on_phase("video_frames")

                def _frames(sf: ScannedFile):
                    return extract_frame_hashes(
                        sf.path, tools, records[sf.path].video_info,
                        check=controller.checkpoint,
                    )

                def _frames_done(sf: ScannedFile, result, exc) -> None:
                    if exc is None and result:
                        records[sf.path].frame_hashes = result

                _parallel(
                    frame_candidates, _frames,
                    workers=max(1, min(config.workers, 4)), controller=controller,
                    handle=_frames_done, emit=cb.on_step_progress,
                    label="Fotogramas de vídeo", started=started,
                )
                video_frames_sampled = len(frame_candidates)

    cancelled = controller.is_cancelled

    # -- 7. group ----------------------------------------------
    cb.on_phase("grouping")
    groups = build_groups(
        records.values(),
        profile=profile,
        enable_perceptual=config.analyze_similar_images,
        enable_video=config.analyze_videos,
        crop_edges=crop_edges,
    )

    # -- 8. cache write --------------------------------------
    if db:
        try:
            db.upsert_many([_to_cached(rec) for rec in records.values()])
        finally:
            db.close()

    cb.on_phase("done")
    return AnalysisResult(
        root=root,
        incremental=incremental,
        scan_stats=scan_stats,
        groups=groups,
        hashed_files=len([r for r in records.values() if r.sha256]),
        elapsed_seconds=time.monotonic() - started,
        cancelled=cancelled,
        decoded_files=decoded_files,
        perceptual_files=perceptual_files,
        cache_hits=cache_hits,
        videos_probed=videos_probed,
        video_frames_sampled=video_frames_sampled,
        ffmpeg_available=ffmpeg_available,
        crop_pairs_found=len(crop_edges),
        sensitivity=getattr(config, "sensitivity", "medium"),
    )


_MAX_CROP_PAIRS = 4000


def _detect_crops(image_recs, controller, cb, workers, started, bands) -> list:
    """Find pairs where one image is a crop of another.

    Candidate pairs: clearly different aspect ratio + similar colour palette
    (same scene) + NOT already very similar by the combined score (a full-frame
    near-duplicate is a resize, not a crop). Bounded for large libraries.
    Returns one best edge per "small" image.
    """
    from app.core.color_hist import histogram_similarity
    from app.core.crop_detect import detect_crop
    from app.core.similarity_engine import Signals, combined_score

    def _sig(r):
        return Signals(r.phash, r.dhash, r.ahash, r.bhash, r.color_sig, r.width, r.height)

    usable = [
        r for r in image_recs
        if r.color_sig and r.width and r.height and not r.error and r.phash is not None
    ]
    pairs: list[tuple] = []
    for i, a in enumerate(usable):
        aa = a.width / a.height if a.height else 0
        for b in usable[i + 1:]:
            ab = b.width / b.height if b.height else 0
            if not aa or not ab:
                continue
            if a.pixel_digest and a.pixel_digest == b.pixel_digest:
                continue
            if a.sha256 and a.sha256 == b.sha256:
                continue
            hs = histogram_similarity(a.color_sig, b.color_sig)
            if hs is None or hs < 50.0:
                continue  # cheap prefilter; detect_crop confirms with NCC
            sc = combined_score(_sig(a), _sig(b)).score
            if sc >= bands.very_similar:
                continue  # already a near-duplicate -> not a crop
            different_aspect = abs(aa - ab) / max(aa, ab) >= 0.05
            # a crop is either a different framing (aspect change) or a
            # proportional sub-region (same aspect, but noticeably less alike)
            if not different_aspect and sc >= bands.similar - 10:
                continue
            if sc < 30.0:
                continue  # nothing in common -> not the same scene at all
            pairs.append((a, b))
            if len(pairs) >= _MAX_CROP_PAIRS:
                break
        if len(pairs) >= _MAX_CROP_PAIRS:
            break

    if not pairs:
        return []

    cb.on_phase("crops")
    found: list = []

    def _one(pair):
        return detect_crop(pair[0].path, pair[1].path)

    def _done(pair, result, exc) -> None:
        if exc is None and result is not None:
            a, b = pair
            found.append((a, b, result.score, result.region))

    _parallel(
        pairs, _one,
        workers=max(1, min(workers, 4)), controller=controller,
        handle=_done, emit=cb.on_step_progress,
        label="Detección de recortes", started=started,
    )

    # keep the single best crop edge per "small" image
    best: dict[int, tuple] = {}
    for a, b, score, region in found:
        small = a if (a.width * a.height) < (b.width * b.height) else b
        cur = best.get(id(small))
        if cur is None or score > cur[2]:
            best[id(small)] = (a, b, score, region)
    return list(best.values())


# ---------------------------------------------------------------------------
def _open_cache(config: AppConfig, db_path: Optional[str]):
    if not config.use_cache and db_path is None:
        return None
    try:
        from app.database.database import FileCacheDB

        db = FileCacheDB(db_path or config.effective_db_path())
        return db if db.available else None
    except Exception as exc:  # noqa: BLE001
        log.warning("Caché deshabilitada: %s", exc)
        return None


def _hex_to_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value, 16)
    except ValueError:
        return None


def _int_to_hex(value: Optional[int]) -> Optional[str]:
    return None if value is None else f"{value:x}"


def _encode_embedding(vec) -> Optional[str]:
    if not vec:
        return None
    from app.core.embeddings import encode

    return encode(vec)


def _to_cached(rec: FileRecord):
    from app.database.models import CachedFile

    info = rec._image_info
    vinfo = rec.video_info
    return CachedFile(
        path=rec.path,
        size=rec.size,
        mtime=rec.mtime,
        sha256=rec.sha256,
        pixel_digest=rec.pixel_digest,
        phash=_int_to_hex(rec.phash),
        dhash=_int_to_hex(rec.dhash),
        ahash=_int_to_hex(rec.ahash),
        bhash=_int_to_hex(rec.bhash),
        color_sig=rec.color_sig,
        embedding=_encode_embedding(rec.embedding),
        width=rec.width,
        height=rec.height,
        img_format=(info.format if info and not info.error else None),
        color_mode=(info.mode if info and not info.error else None),
        orientation=(info.orientation if info and not info.error else None),
        has_exif=(int(info.has_exif) if info and not info.error else None),
        camera_make=(info.camera_make or None if info and not info.error else None),
        camera_model=(info.camera_model or None if info and not info.error else None),
        date_taken=(info.date_taken if info and not info.error else None),
        decode_error=rec.error,
        video_duration=(vinfo.duration if vinfo and vinfo.ok else None),
        video_width=(vinfo.width if vinfo and vinfo.ok else None),
        video_height=(vinfo.height if vinfo and vinfo.ok else None),
        video_codec=(vinfo.video_codec or None if vinfo and vinfo.ok else None),
        video_fps=(vinfo.fps if vinfo and vinfo.ok else None),
        video_bitrate=(vinfo.bitrate if vinfo and vinfo.ok else None),
        video_audio=(vinfo.audio_codec or None if vinfo and vinfo.ok else None),
        frame_hashes=(_encode_frames(rec.frame_hashes) if rec.frame_hashes else None),
        analyzed_at=time.time(),
    )


def _encode_frames(frames) -> Optional[str]:
    from app.core.video_analyzer import encode_frame_hashes

    return encode_frame_hashes(frames)


def _rough_hash_groups(records: Iterable[FileRecord]) -> int:
    seen: dict[str, int] = defaultdict(int)
    for r in records:
        if r.sha256:
            seen[r.sha256] += 1
    return sum(1 for c in seen.values() if c > 1)


def _rough_pixel_groups(records: Iterable[FileRecord]) -> int:
    seen: dict[str, int] = defaultdict(int)
    for r in records:
        if r.pixel_digest:
            seen[r.pixel_digest] += 1
    return sum(1 for c in seen.values() if c > 1)
