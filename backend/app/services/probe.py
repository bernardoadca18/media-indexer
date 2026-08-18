import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProbeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    duration_seconds: float | None
    format_name: str | None
    video_codec: str | None
    audio_codec: str | None
    fps: float | None
    width: int | None
    height: int | None
    raw: dict[str, Any]


class FFprobe:
    def __init__(self, binary: str = "ffprobe"):
        self.binary = binary

    async def inspect(self, path: Path) -> ProbeResult:
        process = await asyncio.create_subprocess_exec(
            self.binary,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            raise ProbeError(stderr.decode(errors="replace").strip() or "ffprobe failed")
        return parse_probe(json.loads(stdout))


def _number(value: Any, cast: type[float] | type[int]) -> float | int | None:
    try:
        return cast(value)
    except (TypeError, ValueError):
        return None


def _fps(value: Any) -> float | None:
    try:
        top, bottom = str(value).split("/", 1)
        return float(top) / float(bottom) if float(bottom) else None
    except (TypeError, ValueError):
        return None


def parse_probe(raw: dict[str, Any]) -> ProbeResult:
    streams = raw.get("streams", [])
    video: dict[str, Any] = next(
        (item for item in streams if item.get("codec_type") == "video"), {}
    )
    audio: dict[str, Any] = next(
        (item for item in streams if item.get("codec_type") == "audio"), {}
    )
    fmt = raw.get("format", {})
    width = _number(video.get("width"), int)
    height = _number(video.get("height"), int)
    return ProbeResult(
        duration_seconds=_number(fmt.get("duration"), float),
        format_name=fmt.get("format_name"),
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name"),
        fps=_fps(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        width=width if isinstance(width, int) else None,
        height=height if isinstance(height, int) else None,
        raw=raw,
    )
