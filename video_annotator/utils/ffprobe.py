"""Thin wrapper around the ffprobe CLI returning typed results.

All calls are synchronous and blocking. Run in a worker thread if needed.
"""

from __future__ import annotations

import fractions
import json
import subprocess
from dataclasses import dataclass


class ProbeError(Exception):
    """Raised when ffprobe fails or returns unexpected output."""


@dataclass
class VideoInfo:
    duration: float
    has_audio: bool
    r_frame_rate: fractions.Fraction      # container's reported frame rate
    avg_frame_rate: fractions.Fraction    # actual average frame rate
    is_vfr: bool                          # r_frame_rate != avg_frame_rate
    codec_name: str                       # e.g. "h264", "hevc", "vp9"
    width: int
    height: int


def probe(path: str) -> VideoInfo:
    """Run ffprobe on path and return a VideoInfo.

    Raises:
        ProbeError: if ffprobe is not found, returns non-zero, or output cannot be parsed.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        path,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        raise ProbeError("ffprobe not found. Ensure FFmpeg is installed and on PATH.")
    except subprocess.TimeoutExpired:
        raise ProbeError(f"ffprobe timed out probing: {path}")

    if result.returncode != 0:
        raise ProbeError(f"ffprobe error (exit {result.returncode}): {result.stderr.strip()}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe output not valid JSON: {exc}") from exc

    streams = data.get("streams", [])

    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )
    if video_stream is None:
        raise ProbeError(f"No video stream found in: {path}")

    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    def _parse_fraction(s: str) -> fractions.Fraction:
        try:
            return fractions.Fraction(s)
        except (ValueError, ZeroDivisionError):
            return fractions.Fraction(0)

    r_frame_rate = _parse_fraction(video_stream.get("r_frame_rate", "0/1"))
    avg_frame_rate = _parse_fraction(video_stream.get("avg_frame_rate", "0/1"))
    is_vfr = r_frame_rate != avg_frame_rate

    # Duration: prefer format-level, fall back to stream-level.
    fmt = data.get("format", {})
    dur_str = fmt.get("duration") or video_stream.get("duration", "0")
    try:
        duration = float(dur_str)
    except (ValueError, TypeError) as exc:
        raise ProbeError(f"Cannot parse duration '{dur_str}' from: {path}") from exc

    return VideoInfo(
        duration=duration,
        has_audio=has_audio,
        r_frame_rate=r_frame_rate,
        avg_frame_rate=avg_frame_rate,
        is_vfr=is_vfr,
        codec_name=video_stream.get("codec_name", ""),
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
    )


def check_videotoolbox_available() -> bool:
    """Return True if ffmpeg was built with h264_videotoolbox encoder support."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-encoders", "-v", "quiet"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "h264_videotoolbox" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
