"""Read-only, bounded FFmpeg black detection for a managed video window."""

import re
import subprocess
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

POLICY_VERSION = "black_window_quality_v1"
MINIMUM_BLACK_DURATION = 0.50
INTERNAL_BLACK_MINIMUM = 0.75
MOSTLY_BLACK_LONGEST = 2.0
MOSTLY_BLACK_RATIO = 0.20
EDGE_TRIM_MARGIN = 0.10
_BLACK = re.compile(r"black_start:([\d.e+-]+)\s+black_end:([\d.e+-]+)")


class VideoWindowRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)

    @model_validator(mode="after")
    def ordered(self) -> "VideoWindowRequest":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be after start_seconds")
        return self


class BlackInterval(BaseModel):
    start_seconds: float
    end_seconds: float


class VideoWindowQuality(BaseModel):
    version: Literal["black_window_quality_v1"] = POLICY_VERSION
    status: Literal["clean", "edge_black", "internal_black", "mostly_black"]
    window_start_seconds: float
    window_end_seconds: float
    black_total_seconds: float
    black_ratio: float
    longest_black_seconds: float
    black_intervals: list[BlackInterval]
    recommended_start_seconds: float | None = None
    recommended_end_seconds: float | None = None


def classify_black_intervals(
    intervals: list[tuple[float, float]],
    start: float,
    end: float,
) -> VideoWindowQuality:
    """Classify relative FFmpeg times and return absolute, authorized ranges."""
    window = VideoWindowRequest(start_seconds=start, end_seconds=end)
    duration = window.end_seconds - window.start_seconds
    merged: list[tuple[float, float]] = []
    for left, right in sorted(intervals):
        left, right = max(0.0, left), min(duration, right)
        if right <= left:
            continue
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    merged = [
        (left, right) for left, right in merged if right - left >= MINIMUM_BLACK_DURATION - 1e-6
    ]
    total = sum(right - left for left, right in merged)
    longest = max((right - left for left, right in merged), default=0.0)
    internal = [
        (left, right)
        for left, right in merged
        if left > EDGE_TRIM_MARGIN and right < duration - EDGE_TRIM_MARGIN
    ]
    status = "clean"
    recommended_start = recommended_end = None
    if longest >= MOSTLY_BLACK_LONGEST or total / duration >= MOSTLY_BLACK_RATIO:
        status = "mostly_black"
    elif any(right - left >= INTERNAL_BLACK_MINIMUM for left, right in internal):
        status = "internal_black"
    elif merged and not internal:
        status = "edge_black"
        recommended_start, recommended_end = start, end
        for left, right in merged:
            if left <= EDGE_TRIM_MARGIN:
                recommended_start = min(end, start + right + EDGE_TRIM_MARGIN)
            if right >= duration - EDGE_TRIM_MARGIN:
                recommended_end = max(start, start + left - EDGE_TRIM_MARGIN)
        if recommended_start >= recommended_end:
            status = "mostly_black"
            recommended_start = recommended_end = None
    return VideoWindowQuality(
        status=status,
        window_start_seconds=start,
        window_end_seconds=end,
        black_total_seconds=total,
        black_ratio=total / duration,
        longest_black_seconds=longest,
        black_intervals=[
            BlackInterval(start_seconds=start + left, end_seconds=start + right)
            for left, right in merged
        ],
        recommended_start_seconds=recommended_start,
        recommended_end_seconds=recommended_end,
    )


def inspect_video_window(
    path: Path,
    request: VideoWindowRequest,
    binary: str = "ffmpeg",
) -> VideoWindowQuality:
    duration = request.end_seconds - request.start_seconds
    try:
        result = subprocess.run(
            [
                binary,
                "-nostdin",
                "-hide_banner",
                "-nostats",
                "-v",
                "info",
                "-ss",
                str(request.start_seconds),
                "-t",
                str(duration),
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                "setpts=PTS-STARTPTS,blackdetect=d=0.5:pix_th=0.10:pic_th=0.95",
                "-progress",
                "pipe:1",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Video window inspection unavailable") from exc
    frames = re.findall(r"^frame=(\d+)$", result.stdout, re.MULTILINE)
    times = re.findall(r"^out_time_us=(\d+)$", result.stdout, re.MULTILINE)
    if (
        result.returncode
        or not frames
        or int(frames[-1]) == 0
        or not times
        or int(times[-1]) / 1_000_000 < duration - EDGE_TRIM_MARGIN
    ):
        raise RuntimeError("Video window could not be completely decoded")
    intervals = [(float(left), float(right)) for left, right in _BLACK.findall(result.stderr)]
    return classify_black_intervals(intervals, request.start_seconds, request.end_seconds)
