import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class SceneDetectionError(RuntimeError):
    pass


class FrameExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SceneResult:
    start_seconds: float
    end_seconds: float
    detector_score: float | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExtractedFrame:
    content: bytes
    mime_type: str
    width: int
    height: int


class SceneDetector(Protocol):
    name: str

    async def detect(
        self, source: Path, duration_seconds: float, threshold: float
    ) -> list[SceneResult]: ...


class FrameExtractor(Protocol):
    name: str

    async def extract(
        self,
        source: Path,
        timestamp_seconds: float,
        width: int,
        image_format: str,
        jpeg_quality: int,
    ) -> ExtractedFrame: ...


def parse_scdet_output(output: str, duration_seconds: float) -> list[SceneResult]:
    cuts: list[tuple[float, float | None]] = []
    score: float | None = None
    for line in output.splitlines():
        if "lavfi.scd.score=" in line:
            try:
                score = float(line.split("lavfi.scd.score=", 1)[1].strip())
            except ValueError:
                score = None
        if "lavfi.scd.time=" in line:
            try:
                timestamp = float(line.split("lavfi.scd.time=", 1)[1].strip())
            except ValueError:
                continue
            if 0 < timestamp < duration_seconds:
                cuts.append((timestamp, score))

    unique: list[tuple[float, float | None]] = []
    for timestamp, cut_score in sorted(cuts):
        if unique and abs(timestamp - unique[-1][0]) < 0.001:
            if cut_score is not None:
                unique[-1] = (unique[-1][0], cut_score)
            continue
        unique.append((timestamp, cut_score))

    starts = [(0.0, None), *unique]
    scenes: list[SceneResult] = []
    for index, (start, start_score) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else duration_seconds
        if end < start:
            continue
        scenes.append(
            SceneResult(
                start_seconds=start,
                end_seconds=end,
                detector_score=start_score,
                metadata={"boundary_source": "ffmpeg_scdet"},
            )
        )
    return scenes


class FFmpegSceneDetector:
    name = "ffmpeg_scdet"

    def __init__(self, binary: str = "ffmpeg"):
        self.binary = binary

    async def detect(
        self, source: Path, duration_seconds: float, threshold: float
    ) -> list[SceneResult]:
        if duration_seconds <= 0:
            raise ValueError("duration must be positive")
        if not 0 <= threshold <= 100:
            raise ValueError("scene threshold must be between 0 and 100")
        process = await asyncio.create_subprocess_exec(
            self.binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(source),
            "-an",
            "-vf",
            f"scdet=t={threshold}:s=1,metadata=print:file=-",
            "-f",
            "null",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode:
            raise SceneDetectionError(
                stderr.decode(errors="replace").strip() or "ffmpeg scene detection failed"
            )
        return parse_scdet_output(stdout.decode(errors="replace"), duration_seconds)


def jpeg_qscale(quality: int) -> int:
    if not 1 <= quality <= 100:
        raise ValueError("jpeg quality must be between 1 and 100")
    return max(2, min(31, round(2 + (100 - quality) * 29 / 99)))


class FFmpegFrameExtractor:
    name = "ffmpeg"

    def __init__(self, binary: str = "ffmpeg"):
        self.binary = binary

    async def extract(
        self,
        source: Path,
        timestamp_seconds: float,
        width: int,
        image_format: str,
        jpeg_quality: int,
    ) -> ExtractedFrame:
        if timestamp_seconds < 0:
            raise ValueError("frame timestamp must be nonnegative")
        if width <= 0:
            raise ValueError("frame width must be positive")
        if image_format != "jpeg":
            raise ValueError("only jpeg representative frames are supported")
        process = await asyncio.create_subprocess_exec(
            self.binary,
            "-hide_banner",
            "-loglevel",
            "info",
            "-nostdin",
            "-ss",
            f"{timestamp_seconds:.6f}",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            f"scale={width}:-2,showinfo",
            "-q:v",
            str(jpeg_qscale(jpeg_quality)),
            "-c:v",
            "mjpeg",
            "-f",
            "image2pipe",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        message = stderr.decode(errors="replace")
        if process.returncode or not stdout:
            raise FrameExtractionError(message.strip() or "ffmpeg frame extraction failed")
        dimensions = re.search(r"\bs:(\d+)x(\d+)\b", message)
        if dimensions is None:
            raise FrameExtractionError("ffmpeg did not report extracted frame dimensions")
        return ExtractedFrame(
            content=stdout,
            mime_type="image/jpeg",
            width=int(dimensions.group(1)),
            height=int(dimensions.group(2)),
        )
