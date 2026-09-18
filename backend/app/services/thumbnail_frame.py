"""Bounded single-frame materialization from a managed source, without a job."""

import subprocess
from pathlib import Path


def extract_frame(path: Path, timestamp: float, width: int, binary: str) -> bytes:
    try:
        result = subprocess.run(
            [
                binary,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-threads",
                "1",
                "-ss",
                str(timestamp),
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-an",
                "-sn",
                "-dn",
                "-vf",
                f"scale=w={width}:h={width}:force_original_aspect_ratio=decrease",
                "-threads",
                "1",
                "-c:v",
                "mjpeg",
                "-q:v",
                "2",
                "-f",
                "image2pipe",
                "pipe:1",
            ],
            capture_output=True,
            timeout=15,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError("Frame extraction unavailable") from None
    if not result.stdout.startswith(b"\xff\xd8") or len(result.stdout) > 8 * 1024 * 1024:
        raise RuntimeError("Invalid frame output")
    return result.stdout
