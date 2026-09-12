import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import ValidationError

from app.api.dependencies import DB, EmbeddingDep, ImageSemanticProviderDep, StorageDep
from app.core.config import settings
from app.repositories.domain import MediaAssetRepository
from app.repositories.image import ImageSemanticIndexRepository
from app.schemas.image import (
    ImageIngestOut,
    ImageProvenanceIn,
    ImageProvenanceOut,
    ImageSemanticIndexJobOut,
    ImageSemanticIndexOut,
)
from app.services.image_ingestion import (
    ImageIngestionService,
    ImageProjectNotFoundError,
    ImageProvenanceConflictError,
    ImageProvenanceTooLargeError,
)
from app.services.image_metadata import InvalidImageError
from app.services.image_semantic_index import (
    ImageSemanticAttemptsExhaustedError,
    ImageSemanticDimensionError,
    ImageSemanticIndexRequest,
    ImageSemanticIndexService,
    ImageSemanticPrerequisiteError,
)
from app.services.storage import StorageLimitError
from app.workers.runner import job_runner

router = APIRouter(tags=["static-images"])


def _index_request(
    vision_provider: ImageSemanticProviderDep,
    embedding_provider: EmbeddingDep,
) -> ImageSemanticIndexRequest:
    return ImageSemanticIndexRequest(
        vision_provider=vision_provider.name,
        vision_model=settings.image_vision_model,
        embedding_provider=embedding_provider.name,
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
    )


@router.get(
    "/media-assets/{media_id}/provenance",
    response_model=ImageProvenanceOut,
)
async def get_image_provenance(media_id: UUID, db: DB) -> object:
    provenance = await MediaAssetRepository(db).provenance(media_id)
    if provenance is None:
        raise HTTPException(404, "image provenance not found")
    return provenance


@router.post("/projects/{project_id}/images", response_model=ImageIngestOut, status_code=202)
async def ingest_image(
    project_id: UUID,
    db: DB,
    storage: StorageDep,
    vision_provider: ImageSemanticProviderDep,
    embedding_provider: EmbeddingDep,
    image: Annotated[UploadFile, File()],
    provenance: Annotated[str, Form()] = "{}",
    enqueue_semantic_index: Annotated[bool, Form()] = True,
) -> ImageIngestOut:
    if len(provenance) > settings.image_max_provenance_characters:
        raise HTTPException(422, "declared provenance exceeds configured limit")
    try:
        provenance_request = ImageProvenanceIn.model_validate(json.loads(provenance))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise HTTPException(422, "provenance must be a valid ImageProvenanceIn JSON object") from exc
    try:
        ingested = await ImageIngestionService(
            db,
            storage,
            max_bytes=settings.image_max_bytes,
            max_pixels=settings.image_max_pixels,
            max_provenance_characters=settings.image_max_provenance_characters,
        ).ingest(project_id, image, provenance_request)
    except ImageProjectNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ImageProvenanceConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (InvalidImageError, StorageLimitError, ImageProvenanceTooLargeError) as exc:
        raise HTTPException(422, str(exc)) from exc

    semantic_index = None
    job = None
    semantic_created = False
    if enqueue_semantic_index:
        try:
            ensured = await ImageSemanticIndexService(db, settings.job_max_attempts).ensure(
                ingested.media.id,
                _index_request(vision_provider, embedding_provider),
            )
        except (
            ImageSemanticPrerequisiteError,
            ImageSemanticAttemptsExhaustedError,
            ImageSemanticDimensionError,
        ) as exc:
            raise HTTPException(409, str(exc)) from exc
        semantic_index = ensured.semantic_index
        job = ensured.job
        semantic_created = ensured.created
        if ensured.enqueue:
            job_runner.enqueue(ensured.job.id)
    return ImageIngestOut(
        media_asset=ingested.media,
        provenance=ingested.provenance,
        media_created=ingested.created,
        semantic_index=semantic_index,
        job=job,
        semantic_index_created=semantic_created,
    )


@router.post(
    "/media-assets/{media_id}/image-semantic-indexes",
    response_model=ImageSemanticIndexJobOut,
    status_code=202,
)
async def ensure_image_semantic_index(
    media_id: UUID,
    db: DB,
    vision_provider: ImageSemanticProviderDep,
    embedding_provider: EmbeddingDep,
) -> ImageSemanticIndexJobOut:
    try:
        result = await ImageSemanticIndexService(db, settings.job_max_attempts).ensure(
            media_id,
            _index_request(vision_provider, embedding_provider),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (
        ImageSemanticPrerequisiteError,
        ImageSemanticAttemptsExhaustedError,
        ImageSemanticDimensionError,
    ) as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.enqueue:
        job_runner.enqueue(result.job.id)
    return ImageSemanticIndexJobOut(
        semantic_index=result.semantic_index,
        job=result.job,
        created=result.created,
    )


@router.get(
    "/media-assets/{media_id}/image-semantic-indexes",
    response_model=list[ImageSemanticIndexOut],
)
async def list_image_semantic_indexes(media_id: UUID, db: DB) -> object:
    return await ImageSemanticIndexRepository(db).list_for_media(media_id)


@router.get(
    "/image-semantic-indexes/{image_semantic_index_id}",
    response_model=ImageSemanticIndexOut,
)
async def get_image_semantic_index(image_semantic_index_id: UUID, db: DB) -> object:
    item = await ImageSemanticIndexRepository(db).get(image_semantic_index_id)
    if item is None:
        raise HTTPException(404, "image semantic index not found")
    return item
