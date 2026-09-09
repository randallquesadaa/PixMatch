"""Video analysis: metadata, frame-hash similarity, grouping, cache."""

from __future__ import annotations

import shutil

from app.config import AppConfig
from app.core.analysis import run_analysis
from app.core.similarity import MatchCategory
from app.core.video_analyzer import (
    compare_frame_hashes,
    decode_frame_hashes,
    encode_frame_hashes,
    extract_frame_hashes,
    probe_video,
)


def _cfg(**kw) -> AppConfig:
    c = AppConfig(analyze_images=False, analyze_videos=True, workers=3, use_cache=False)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


# -- metadata -------------------------------------------------------
def test_probe_reads_core_metadata(tmp_path, make_video, ffmpeg_tools):
    v = make_video(tmp_path / "clip.mp4", duration=2, size=(176, 144), rate=15)
    info = probe_video(str(v), ffmpeg_tools)
    assert info.ok
    assert 1.6 < info.duration < 2.6
    assert (info.width, info.height) == (176, 144)
    assert info.video_codec == "h264"
    assert 13 < info.fps < 17
    assert info.has_audio


def test_probe_missing_file(tmp_path, ffmpeg_tools):
    info = probe_video(str(tmp_path / "nope.mp4"), ffmpeg_tools)
    assert info.error and not info.ok


# -- frame hashes -------------------------------------------------
def test_frame_hashes_and_comparison(tmp_path, make_video, ffmpeg_tools):
    a = make_video(tmp_path / "a.mp4", pattern="testsrc", duration=3)
    a_reenc = tmp_path / "a_reenc.mp4"
    import subprocess

    subprocess.run(
        [
            ffmpeg_tools.ffmpeg,
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(a),
            "-c:v",
            "libx264",
            "-crf",
            "38",
            "-c:a",
            "aac",
            str(a_reenc),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    b = make_video(tmp_path / "b.mp4", pattern="testsrc2", duration=3)

    ia = probe_video(str(a), ffmpeg_tools)
    ir = probe_video(str(a_reenc), ffmpeg_tools)
    ib = probe_video(str(b), ffmpeg_tools)

    ha = extract_frame_hashes(str(a), ffmpeg_tools, ia)
    hr = extract_frame_hashes(str(a_reenc), ffmpeg_tools, ir)
    hb = extract_frame_hashes(str(b), ffmpeg_tools, ib)

    assert len([h for h in ha if h is not None]) >= 4
    assert compare_frame_hashes(ha, ha) == 100.0
    assert compare_frame_hashes(ha, hr) >= 90.0  # re-encode stays close
    assert compare_frame_hashes(ha, hb) < 80.0  # different content


def test_compare_needs_overlap():
    assert compare_frame_hashes([1], [1]) is None
    assert compare_frame_hashes([None, None], [1, 2]) is None
    assert compare_frame_hashes([], []) is None


def test_frame_hash_serialisation():
    data = [123, None, 456]
    encoded = encode_frame_hashes(data)
    assert encoded == "000000000000007b,-,00000000000001c8"
    assert decode_frame_hashes(encoded) == data
    assert decode_frame_hashes(None) == []
    assert decode_frame_hashes("garbage,,-") == [None, None, None]


# -- grouping through the pipeline -------------------------------
def test_byte_identical_videos_are_file_identical(tmp_path, make_video):
    a = make_video(tmp_path / "movie.mp4")
    shutil.copy2(a, tmp_path / "movie (copy).mp4")
    result = run_analysis(str(tmp_path), _cfg())
    assert len(result.groups) == 1
    assert result.groups[0].category is MatchCategory.FILE_IDENTICAL


def test_reencoded_video_grouped_as_probable_same_content(tmp_path, make_video, ffmpeg_tools):
    a = make_video(tmp_path / "orig.mp4", duration=3)
    import subprocess

    subprocess.run(
        [
            ffmpeg_tools.ffmpeg,
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(a),
            "-c:v",
            "libx264",
            "-crf",
            "30",
            "-c:a",
            "aac",
            str(tmp_path / "orig_recompressed.mp4"),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    result = run_analysis(str(tmp_path), _cfg(similarity_threshold=85))
    assert len(result.groups) == 1
    g = result.groups[0]
    assert g.category in (
        MatchCategory.SAME_CONTENT_LIKELY,
        MatchCategory.VERY_SIMILAR,
        MatchCategory.SIMILAR,
    )
    assert g.similarity_percent is not None


def test_unrelated_videos_not_grouped(tmp_path, make_video):
    make_video(tmp_path / "one.mp4", pattern="testsrc", duration=3)
    make_video(tmp_path / "two.mp4", pattern="testsrc2", duration=3)
    assert run_analysis(str(tmp_path), _cfg()).groups == []


def test_byte_identical_plus_reencode_land_in_one_group(tmp_path, make_video, ffmpeg_tools):
    a = make_video(tmp_path / "v.mp4", duration=3)
    shutil.copy2(a, tmp_path / "v_backup.mp4")
    import subprocess

    subprocess.run(
        [
            ffmpeg_tools.ffmpeg,
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(a),
            "-c:v",
            "libx264",
            "-crf",
            "32",
            "-c:a",
            "aac",
            str(tmp_path / "v_small.mp4"),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    result = run_analysis(str(tmp_path), _cfg(similarity_threshold=85))
    assert len(result.groups) == 1
    assert result.groups[0].count == 3


def test_video_analysis_without_ffmpeg_does_not_crash(tmp_path, make_video, monkeypatch):
    make_video(tmp_path / "a.mp4")
    shutil.copy2(tmp_path / "a.mp4", tmp_path / "b.mp4")

    from app.core import ffmpeg as ffmpeg_mod

    monkeypatch.setattr(ffmpeg_mod, "detect", lambda *a, **k: ffmpeg_mod.FfmpegTools(source="none"))
    result = run_analysis(str(tmp_path), _cfg())
    assert result.ffmpeg_available is False
    # byte-identical files are still caught by SHA-256
    assert len(result.groups) == 1
    assert result.groups[0].category is MatchCategory.FILE_IDENTICAL


def test_incremental_reuses_video_cache(tmp_path, make_video, ffmpeg_tools):
    db = tmp_path / "vc.sqlite3"
    a = make_video(tmp_path / "a.mp4", duration=3)
    import subprocess

    subprocess.run(
        [
            ffmpeg_tools.ffmpeg,
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(a),
            "-c:v",
            "libx264",
            "-crf",
            "30",
            "-c:a",
            "aac",
            str(tmp_path / "a2.mp4"),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    first = run_analysis(str(tmp_path), _cfg(), db_path=str(db))
    assert first.videos_probed == 2

    second = run_analysis(str(tmp_path), _cfg(), db_path=str(db), incremental=True)
    assert second.cache_hits >= 2
    assert second.video_frames_sampled == 0  # reused from cache
    assert len(second.groups) == len(first.groups)
