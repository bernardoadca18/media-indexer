import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routers import media_assets
from app.services.storage import LocalStorage
from app.services.video_window_quality import (
    VideoWindowRequest,
    classify_black_intervals,
    inspect_video_window,
)


@pytest.mark.parametrize(
    "intervals,status",
    [
        ([], "clean"),
        ([(4, 4.6)], "clean"),
        ([(0, 0.49)], "clean"),
        ([(0, 0.6)], "edge_black"),
        ([(4, 4.8)], "internal_black"),
        ([(4, 6)], "mostly_black"),
        ([(1, 2), (3, 4)], "mostly_black"),
        ([(0, 0.6), (9.4, 10)], "edge_black"),
    ],
)
def test_black_interval_classification_uses_absolute_authorized_ranges(intervals, status):
    result = classify_black_intervals(intervals, 100, 110)
    assert result.status == status
    assert all(
        100 <= item.start_seconds < item.end_seconds <= 110 for item in result.black_intervals
    )
    if status == "edge_black":
        assert result.recommended_start_seconds == pytest.approx(100.7)
        assert 100 <= result.recommended_start_seconds < result.recommended_end_seconds <= 110


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_ffmpeg_reads_only_requested_window(tmp_path):
    path = tmp_path / "four-seconds.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=64x64:r=25:d=0.6",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=64x64:r=25:d=3.4",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0",
            "-c:v",
            "ffv1",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert (
        inspect_video_window(path, VideoWindowRequest(start_seconds=0, end_seconds=4)).status
        == "edge_black"
    )
    assert (
        inspect_video_window(path, VideoWindowRequest(start_seconds=1, end_seconds=3)).status
        == "clean"
    )
    assert (
        inspect_video_window(path, VideoWindowRequest(start_seconds=0, end_seconds=0.6)).status
        == "mostly_black"
    )


@pytest.mark.asyncio
async def test_window_endpoint_resolves_managed_storage_and_rejects_out_of_bounds(
    monkeypatch, tmp_path
):
    media_id = uuid4()
    path = tmp_path / "managed.mp4"
    path.touch()
    asset = SimpleNamespace(
        media_kind="video", duration_seconds=20, storage_uri="local://managed.mp4"
    )
    monkeypatch.setattr(media_assets, "_managed_asset", AsyncMock(return_value=asset))

    def inspect(source, request, binary):
        assert source == path
        return classify_black_intervals([], request.start_seconds, request.end_seconds)

    monkeypatch.setattr(media_assets, "inspect_video_window", inspect)
    result = await media_assets.video_window_quality(
        media_id,
        VideoWindowRequest(start_seconds=10, end_seconds=15),
        None,
        LocalStorage(tmp_path),
    )
    assert result.status == "clean"
    assert str(tmp_path) not in result.model_dump_json()
    with pytest.raises(HTTPException) as error:
        await media_assets.video_window_quality(
            media_id,
            VideoWindowRequest(start_seconds=10, end_seconds=25),
            None,
            LocalStorage(tmp_path),
        )
    assert error.value.status_code == 422
