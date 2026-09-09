"""Read-only video inspection: metadata + a handful of representative frame
hashes. Never re-encodes or modifies the source.

Levels of evidence (kept separate, per requirement 12):
  * byte-identical file        -> FILE_IDENTICAL (handled by SHA-256)
  * matching sampled frames    -> SAME_CONTENT_LIKELY  (never "identical")
  * close sampled frames       -> VERY_SIMILAR / SIMILAR

Only a few frames are sampled (default 5), so a match is always reported as
"probable", never certain.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from statistics import mean
from typing import Callable, Optional

from app.core.ffmpeg import FfmpegTools, _no_window
from app.core.perceptual import hamming, perceptual_hash_bytes, similarity_percent
from app.utils.file_utils import extended_path
from app.utils.logging_setup import get_logger

log = get_logger(__name__)

FRAME_POSITIONS = (0.05, 0.25, 0.5, 0.75, 0.95)
_FRAME_TIMEOUT = 45
_PROBE_TIMEOUT = 30


@dataclass
class VideoInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    video_codec: str = ""
    fps: float = 0.0
    bitrate: int = 0
    has_audio: bool = False
    audio_codec: str = ""
    creation_time: Optional[str] = None
    error: Optional[str] = None

    @property
    def resolution(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def ok(self) -> bool:
        return self.error is None and self.duration > 0


# ---------------------------------------------------------------------------
def probe_video(path: str, tools: FfmpegTools) -> VideoInfo:
    if tools.ffprobe:
        info = _probe_with_ffprobe(path, tools.ffprobe)
        if info.ok or tools.ffmpeg is None:
            return info
    if tools.ffmpeg:
        return _probe_with_ffmpeg(path, tools.ffmpeg)
    return VideoInfo(error="FFmpeg no está disponible.")


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, timeout=timeout, creationflags=_no_window()
    )


def _probe_with_ffprobe(path: str, ffprobe: str) -> VideoInfo:
    cmd = [
        ffprobe, "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", extended_path(path),
    ]
    try:
        result = _run(cmd, _PROBE_TIMEOUT)
        data = json.loads(result.stdout or b"{}")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return VideoInfo(error=f"ffprobe falló: {exc}")

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        return VideoInfo(error="El archivo no contiene pista de vídeo.")

    info = VideoInfo()
    info.duration = _to_float(fmt.get("duration")) or _to_float(video.get("duration"))
    info.width = int(video.get("width") or 0)
    info.height = int(video.get("height") or 0)
    info.video_codec = str(video.get("codec_name") or "")
    info.fps = _parse_fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    info.bitrate = int(_to_float(fmt.get("bit_rate")) or _to_float(video.get("bit_rate")) or 0)
    info.has_audio = audio is not None
    info.audio_codec = str(audio.get("codec_name") or "") if audio else ""
    tags = fmt.get("tags", {}) or {}
    info.creation_time = tags.get("creation_time")
    if info.duration <= 0:
        info.error = "Duración desconocida."
    return info


_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)")
_BITRATE_RE = re.compile(r"bitrate:\s*(\d+)\s*kb/s")
_VIDEO_RE = re.compile(
    r"Stream #\d+:\d+.*?: Video:\s*([\w0-9]+).*?(\d{2,5})x(\d{2,5})", re.DOTALL
)
_FPS_RE = re.compile(r"([\d.]+)\s*fps")
_AUDIO_RE = re.compile(r"Stream #\d+:\d+.*?: Audio:\s*([\w0-9]+)")
_CREATION_RE = re.compile(r"creation_time\s*:\s*(\S+)")


def _probe_with_ffmpeg(path: str, ffmpeg: str) -> VideoInfo:
    try:
        result = _run([ffmpeg, "-nostdin", "-hide_banner", "-i", extended_path(path)], _PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return VideoInfo(error=f"ffmpeg falló: {exc}")
    text = (result.stderr or b"").decode("utf-8", "replace")

    info = VideoInfo()
    m = _DUR_RE.search(text)
    if m:
        h, mm, ss = m.groups()
        info.duration = int(h) * 3600 + int(mm) * 60 + float(ss)
    m = _BITRATE_RE.search(text)
    if m:
        info.bitrate = int(m.group(1)) * 1000
    m = _VIDEO_RE.search(text)
    if m:
        info.video_codec = m.group(1)
        info.width, info.height = int(m.group(2)), int(m.group(3))
    # look for fps only on the Video stream line
    vline = next((ln for ln in text.splitlines() if ": Video:" in ln), "")
    m = _FPS_RE.search(vline)
    if m:
        info.fps = float(m.group(1))
    m = _AUDIO_RE.search(text)
    if m:
        info.has_audio = True
        info.audio_codec = m.group(1)
    m = _CREATION_RE.search(text)
    if m:
        info.creation_time = m.group(1)

    if info.width == 0 and info.duration == 0:
        info.error = "No se pudo leer la información del vídeo."
    elif info.duration <= 0:
        info.error = "Duración desconocida."
    return info


# ---------------------------------------------------------------------------
def extract_frame_hashes(
    path: str,
    tools: FfmpegTools,
    info: VideoInfo,
    *,
    positions: tuple[float, ...] = FRAME_POSITIONS,
    check: Optional[Callable[[], bool]] = None,
) -> list[Optional[int]]:
    """Sample one frame at each relative position and return its pHash.
    Entries are ``None`` where a frame could not be grabbed."""
    if not tools.ffmpeg or not info.ok:
        return []
    hashes: list[Optional[int]] = []
    for pos in positions:
        if check is not None and not check():
            break
        timestamp = max(0.0, min(info.duration - 0.1, pos * info.duration))
        png = _grab_frame_png(path, tools.ffmpeg, timestamp)
        hashes.append(perceptual_hash_bytes(png) if png else None)
    return hashes


def _grab_frame_png(path: str, ffmpeg: str, timestamp: float) -> Optional[bytes]:
    cmd = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", f"{timestamp:.3f}", "-i", extended_path(path),
        "-frames:v", "1", "-vf", "scale=320:-2",
        "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    try:
        result = _run(cmd, _FRAME_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("frame grab failed (%s @ %.2fs): %s", path, timestamp, exc)
        return None
    return result.stdout if result.returncode == 0 and result.stdout else None


def compare_frame_hashes(
    a: list[Optional[int]], b: list[Optional[int]]
) -> Optional[float]:
    """Average per-position perceptual similarity, or ``None`` if there is not
    enough overlap to judge."""
    pairs = [
        (x, y) for x, y in zip(a, b) if x is not None and y is not None
    ]
    if len(pairs) < 2:
        return None
    sims = [similarity_percent(hamming(x, y)) for x, y in pairs]
    return round(mean(sims), 1)


# ---------------------------------------------------------------------------
def encode_frame_hashes(hashes: list[Optional[int]]) -> str:
    return ",".join("-" if h is None else f"{h:016x}" for h in hashes)


def decode_frame_hashes(text: Optional[str]) -> list[Optional[int]]:
    if not text:
        return []
    out: list[Optional[int]] = []
    for token in text.split(","):
        token = token.strip()
        if token in ("", "-"):
            out.append(None)
        else:
            try:
                out.append(int(token, 16))
            except ValueError:
                out.append(None)
    return out


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_fraction(value) -> float:
    if not value:
        return 0.0
    try:
        if "/" in str(value):
            num, den = str(value).split("/")
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f else 0.0
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
