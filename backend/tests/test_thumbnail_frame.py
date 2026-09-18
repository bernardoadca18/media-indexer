import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api.routers import media_assets
from app.services.thumbnail_frame import extract_frame


@pytest.mark.asyncio
async def test_frame_route_bounds_and_no_paths(tmp_path, monkeypatch):
    path = tmp_path / "secret.mp4"
    path.write_bytes(b"video")
    asset = SimpleNamespace(
        media_kind="video", duration_seconds=10, storage_uri="managed", sha256="a" * 64
    )
    monkeypatch.setattr(media_assets, "_managed_asset", AsyncMock(return_value=asset))
    monkeypatch.setattr(media_assets, "extract_frame", lambda *args: b"\xff\xd8frame")
    storage = SimpleNamespace(resolve_any=lambda uri: path)
    result = await media_assets.get_media_asset_frame(uuid4(), None, storage, 2.0, 1600)
    assert result.media_type == "image/jpeg" and result.body == b"\xff\xd8frame"
    assert str(path) not in str(result.headers)
    with pytest.raises(HTTPException) as error:
        await media_assets.get_media_asset_frame(uuid4(), None, storage, 10.0, 1600)
    assert error.value.status_code == 422
    asset.media_kind = "image"
    with pytest.raises(HTTPException):
        await media_assets.get_media_asset_frame(uuid4(), None, storage, 0, 1600)


def test_bounded_seek_and_single_frame(monkeypatch, tmp_path):
    def run(args, **kw):
        assert args.index("-ss") < args.index("-i")
        assert args[args.index("-frames:v") + 1] == "1" and kw["timeout"] == 15
        return SimpleNamespace(stdout=b"\xff\xd8frame")

    monkeypatch.setattr(subprocess, "run", run)
    assert extract_frame(tmp_path / "source.mp4", 4.5, 1600, "ffmpeg") == b"\xff\xd8frame"
