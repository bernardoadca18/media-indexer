import asyncio
import re
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.api.dependencies import DB, StorageDep
from app.core.config import settings
from app.models import MediaAsset
from app.repositories.domain import MediaAssetRepository
from app.schemas.domain import MediaAssetMetadataOut
from app.services.thumbnail_frame import extract_frame
from app.services.video_window_quality import (
    VideoWindowQuality,
    VideoWindowRequest,
    inspect_video_window,
)

router = APIRouter(prefix="/media-assets", tags=["media-assets"])


async def _managed_asset(media_id: UUID, db: DB) -> MediaAsset:
    item = await MediaAssetRepository(db).get(media_id)
    if item is None:
        raise HTTPException(404, "Media asset not found")
    return item


def _download_name(media_id: UUID, original_filename: str) -> str:
    suffix = Path(original_filename).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ".bin"
    return f"{media_id}{suffix}"


@router.get("/{media_id}", response_model=MediaAssetMetadataOut)
async def get_media_asset(media_id: UUID, db: DB) -> object:
    """Return immutable source identity without exposing storage_uri or host paths."""
    return await _managed_asset(media_id, db)


@router.get("/{media_id}/content")
async def get_media_asset_content(media_id: UUID, db: DB, storage: StorageDep) -> FileResponse:
    """Stream only a DB-owned MediaAsset resolved by the configured managed storage."""
    item = await _managed_asset(media_id, db)
    try:
        path = storage.resolve_any(item.storage_uri)
    except ValueError as exc:
        raise HTTPException(404, "Managed media content unavailable") from exc
    if not path.is_file():
        raise HTTPException(404, "Managed media content unavailable")
    return FileResponse(
        path,
        media_type=item.content_type or "application/octet-stream",
        filename=_download_name(item.id, item.original_filename),
        headers={
            "Accept-Ranges": "bytes",
            "ETag": f'"{item.sha256}"',
            "X-Content-SHA256": item.sha256,
        },
    )


@router.post("/{media_id}/video-window-quality", response_model=VideoWindowQuality)
async def video_window_quality(
    media_id: UUID,
    request: VideoWindowRequest,
    db: DB,
    storage: StorageDep,
) -> VideoWindowQuality:
    item = await _managed_asset(media_id, db)
    if item.media_kind != "video":
        raise HTTPException(422, "Video window inspection requires a video asset")
    if (
        item.duration_seconds is None
        or request.end_seconds > item.duration_seconds
        or request.end_seconds - request.start_seconds > settings.candidate_max_duration_seconds
    ):
        raise HTTPException(422, "Window exceeds the asset or candidate duration limit")
    try:
        path = storage.resolve_any(item.storage_uri)
    except ValueError as exc:
        raise HTTPException(404, "Managed media content unavailable") from exc
    if not path.is_file():
        raise HTTPException(404, "Managed media content unavailable")
    try:
        return await asyncio.to_thread(inspect_video_window, path, request, settings.ffmpeg_binary)
    except RuntimeError as exc:
        raise HTTPException(503, "Video window inspection unavailable") from exc


@router.get("/{media_id}/frame")
async def get_media_asset_frame(
    media_id: UUID,
    db: DB,
    storage: StorageDep,
    timestamp_seconds: float = Query(ge=0, allow_inf_nan=False),
    width: int = Query(default=1600, ge=160, le=1920),
) -> Response:
    item = await _managed_asset(media_id, db)
    if item.media_kind != "video" or item.duration_seconds is None:
        raise HTTPException(422, "Frame requires a video with known duration")
    if timestamp_seconds >= item.duration_seconds:
        raise HTTPException(422, "Timestamp exceeds video duration")
    try:
        path = storage.resolve_any(item.storage_uri)
        if not path.is_file():
            raise ValueError("Missing managed source")
    except ValueError:
        raise HTTPException(404, "Managed media content unavailable") from None
    try:
        data = await asyncio.to_thread(
            extract_frame, path, timestamp_seconds, width, settings.ffmpeg_binary
        )
    except RuntimeError:
        raise HTTPException(503, "Frame extraction unavailable") from None
    return Response(data, media_type="image/jpeg", headers={"X-Source-SHA256": item.sha256})
