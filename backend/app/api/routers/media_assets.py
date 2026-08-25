import re
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.dependencies import DB, StorageDep
from app.models import MediaAsset
from app.repositories.domain import MediaAssetRepository
from app.schemas.domain import MediaAssetMetadataOut

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
        path = storage.resolve(item.storage_uri)
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
