import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import structlog
from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaAsset, MediaAssetProvenance
from app.repositories.domain import MediaAssetRepository, ProjectRepository
from app.schemas.image import ImageProvenanceIn
from app.services.image_metadata import ImageTechnicalMetadata, inspect_static_image
from app.services.storage import Storage

logger = structlog.get_logger(__name__)


class ImageProjectNotFoundError(LookupError):
    pass


class ImageProvenanceConflictError(ValueError):
    pass


class ImageProvenanceTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class ImageIngestionResult:
    media: MediaAsset
    provenance: MediaAssetProvenance
    created: bool


def _clean_optional(value: str | None) -> str | None:
    return " ".join(value.split()) if value is not None else None


def canonical_provenance(payload: ImageProvenanceIn) -> dict[str, Any]:
    return {
        "source_owner": _clean_optional(payload.source_owner),
        "source_class": _clean_optional(payload.source_class),
        "asset_class": _clean_optional(payload.asset_class),
        "collection": _clean_optional(payload.collection),
        "game": _clean_optional(payload.game),
        "declared_metadata": payload.declared_metadata,
    }


def provenance_fingerprint(provenance: dict[str, Any]) -> str:
    canonical = json.dumps(
        provenance, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ImageIngestionService:
    def __init__(
        self,
        db: AsyncSession,
        storage: Storage,
        *,
        max_bytes: int,
        max_pixels: int,
        max_provenance_characters: int,
    ) -> None:
        self.db = db
        self.storage = storage
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels
        self.max_provenance_characters = max_provenance_characters

    async def ingest(
        self,
        project_id: UUID,
        image: UploadFile,
        provenance_request: ImageProvenanceIn,
    ) -> ImageIngestionResult:
        if await ProjectRepository(self.db).get(project_id) is None:
            raise ImageProjectNotFoundError("project not found")

        provenance_values = canonical_provenance(provenance_request)
        canonical_json = json.dumps(
            provenance_values, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        if len(canonical_json) > self.max_provenance_characters:
            raise ImageProvenanceTooLargeError("declared provenance exceeds configured limit")
        fingerprint = provenance_fingerprint(provenance_values)

        media_id = uuid4()
        stored = await self.storage.save_media_source(
            project_id, media_id, image, self.max_bytes
        )
        try:
            technical = await inspect_static_image(
                self.storage.resolve(stored.uri),
                image.content_type,
                max_pixels=self.max_pixels,
            )
            repository = MediaAssetRepository(self.db)
            existing = await repository.image_by_checksum(project_id, stored.sha256)
            if existing is not None:
                await self.storage.delete(stored.uri)
                return await self._reuse(existing, fingerprint)
            return await self._create(
                project_id,
                media_id,
                image.filename or "image.bin",
                stored.uri,
                stored.byte_size,
                stored.sha256,
                technical,
                provenance_values,
                fingerprint,
            )
        except Exception:
            await self.db.rollback()
            await self.storage.delete(stored.uri)
            raise

    async def _reuse(
        self, media: MediaAsset, expected_fingerprint: str
    ) -> ImageIngestionResult:
        provenance = await MediaAssetRepository(self.db).provenance(media.id)
        if provenance is None:
            raise RuntimeError("image asset exists without declared provenance")
        if provenance.fingerprint != expected_fingerprint:
            raise ImageProvenanceConflictError(
                "image content already exists in this project with different immutable provenance"
            )
        logger.info(
            "image_ingest_reused",
            project_id=str(media.project_id),
            media_id=str(media.id),
        )
        return ImageIngestionResult(media, provenance, False)

    async def _create(
        self,
        project_id: UUID,
        media_id: UUID,
        original_filename: str,
        storage_uri: str,
        byte_size: int,
        sha256: str,
        technical: ImageTechnicalMetadata,
        provenance_values: dict[str, Any],
        fingerprint: str,
    ) -> ImageIngestionResult:
        media = MediaAsset(
            id=media_id,
            project_id=project_id,
            storage_uri=storage_uri,
            original_filename=original_filename.replace("\\", "/").rsplit("/", 1)[-1],
            content_type=technical.content_type,
            byte_size=byte_size,
            sha256=sha256,
            media_kind="image",
            duration_seconds=None,
            format_name=technical.format_name,
            video_codec=None,
            audio_codec=None,
            fps=None,
            width=technical.width,
            height=technical.height,
            probe_metadata={
                "technical_metadata_version": "static_image_v1",
                "detected_format": technical.format_name,
                "detected_mime": technical.content_type,
            },
        )
        provenance = MediaAssetProvenance(
            project_id=project_id,
            media_id=media_id,
            fingerprint=fingerprint,
            **provenance_values,
        )
        self.db.add_all([media, provenance])
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            await self.storage.delete(storage_uri)
            existing = await MediaAssetRepository(self.db).image_by_checksum(project_id, sha256)
            if existing is None:
                raise exc
            return await self._reuse(existing, fingerprint)
        await self.db.refresh(media)
        await self.db.refresh(provenance)
        logger.info(
            "image_ingest_created",
            project_id=str(project_id),
            media_id=str(media.id),
            content_type=media.content_type,
            byte_size=media.byte_size,
        )
        return ImageIngestionResult(media, provenance, True)
