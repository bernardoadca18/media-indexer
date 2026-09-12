import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EMBEDDING_VECTOR_DIMENSIONS,
    ImageSemanticIndex,
    MediaAsset,
    MediaAssetProvenance,
    ProcessingJob,
)
from app.repositories.domain import JobRepository, MediaAssetRepository
from app.repositories.image import ImageSemanticIndexRepository
from app.services.embedding_provider import EmbeddingProvider, validate_embeddings
from app.services.image_semantic_prompt import (
    IMAGE_SEMANTIC_PROMPT_VERSION,
    IMAGE_SEMANTIC_RESULT_SCHEMA_VERSION,
)
from app.services.image_semantic_provider import ImageSemanticProvider, ImageSemanticResult
from app.services.storage import Storage

logger = structlog.get_logger(__name__)

IMAGE_SEMANTIC_STRATEGY = "vision_caption_text_embedding_v1"
IMAGE_CANONICAL_TEXT_VERSION = "image_search_text_v1"


class ImageSemanticPrerequisiteError(ValueError):
    pass


class ImageSemanticAttemptsExhaustedError(ValueError):
    pass


class ImageSemanticDimensionError(ValueError):
    pass


@dataclass(frozen=True)
class ImageSemanticIndexRequest:
    vision_provider: str
    vision_model: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    strategy: str = IMAGE_SEMANTIC_STRATEGY
    prompt_version: str = IMAGE_SEMANTIC_PROMPT_VERSION
    result_schema_version: str = IMAGE_SEMANTIC_RESULT_SCHEMA_VERSION
    canonical_text_version: str = IMAGE_CANONICAL_TEXT_VERSION


@dataclass(frozen=True)
class EnsureImageSemanticIndexResult:
    semantic_index: ImageSemanticIndex
    job: ProcessingJob
    enqueue: bool
    created: bool


def image_configuration_fingerprint(configuration: dict[str, Any]) -> str:
    canonical = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _clean(value: str) -> str:
    return " ".join(value.split())


def _declared_value(value: Any) -> str | None:
    if isinstance(value, str):
        return _clean(value) or None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list) and all(isinstance(item, str | int | float | bool) for item in value):
        rendered = [_declared_value(item) for item in value]
        return ", ".join(item for item in rendered if item)
    return None


def build_canonical_image_text(
    provenance: MediaAssetProvenance,
    result: ImageSemanticResult,
) -> str:
    """Build deterministic, versioned retrieval text without embedding arbitrary JSON."""
    parts = [f"Canonical text version: {IMAGE_CANONICAL_TEXT_VERSION}."]
    declared = (
        ("Source owner", provenance.source_owner),
        ("Source class", provenance.source_class),
        ("Asset class", provenance.asset_class),
        ("Collection", provenance.collection),
        ("Game", provenance.game),
    )
    for label, value in declared:
        if value:
            parts.append(f"Declared {label.casefold()}: {_clean(value)}.")
    for key in sorted(provenance.declared_metadata):
        rendered = _declared_value(provenance.declared_metadata[key])
        if rendered:
            label = " ".join(key.replace("_", " ").split())
            parts.append(f"Declared {label}: {rendered}.")

    parts.append(f"Visual summary: {_clean(result.summary)}")
    parts.append(f"Visual description: {_clean(result.description)}")
    fields: tuple[tuple[str, list[str]], ...] = (
        ("Subjects", result.subjects),
        ("Environment", result.environment),
        ("Activities", result.activities),
        ("Vehicles", result.vehicles),
        ("Objects", result.objects),
        ("Architecture", result.architecture),
        ("Visual features", result.visual_features),
        ("Clothing", result.clothing),
        ("Composition", result.composition),
        ("Visible text", result.visible_text),
        ("Search terms", result.search_terms),
        ("Uncertainties", result.uncertainties),
    )
    for label, values in fields:
        cleaned = [_clean(value) for value in values if _clean(value)]
        if cleaned:
            parts.append(f"{label}: {', '.join(cleaned)}.")
    if result.time_of_day:
        parts.append(f"Time of day: {_clean(result.time_of_day)}.")
    if result.weather:
        parts.append(f"Weather: {_clean(result.weather)}.")
    return "\n".join(parts)


class ImageSemanticIndexService:
    def __init__(self, db: AsyncSession, max_attempts: int) -> None:
        self.db = db
        self.max_attempts = max_attempts

    async def ensure(
        self,
        media_id: UUID,
        request: ImageSemanticIndexRequest,
    ) -> EnsureImageSemanticIndexResult:
        media = await MediaAssetRepository(self.db).get(media_id)
        if media is None:
            raise LookupError("media asset not found")
        if media.media_kind != "image":
            raise ImageSemanticPrerequisiteError("media asset is not a source static image")
        supported_versions = (
            request.strategy == IMAGE_SEMANTIC_STRATEGY
            and request.prompt_version == IMAGE_SEMANTIC_PROMPT_VERSION
            and request.result_schema_version == IMAGE_SEMANTIC_RESULT_SCHEMA_VERSION
            and request.canonical_text_version == IMAGE_CANONICAL_TEXT_VERSION
        )
        if not supported_versions:
            raise ImageSemanticPrerequisiteError("unsupported image semantic strategy version")
        if request.embedding_dimensions != EMBEDDING_VECTOR_DIMENSIONS:
            raise ImageSemanticDimensionError(
                f"database vector dimension is {EMBEDDING_VECTOR_DIMENSIONS}; "
                f"configured {request.embedding_dimensions}"
            )
        provenance = await MediaAssetRepository(self.db).provenance(media_id)
        if provenance is None:
            raise ImageSemanticPrerequisiteError("image has no declared provenance")

        configuration: dict[str, Any] = {
            "strategy": request.strategy,
            "prompt_version": request.prompt_version,
            "result_schema_version": request.result_schema_version,
            "canonical_text_version": request.canonical_text_version,
            "vision_provider": request.vision_provider,
            "vision_model": request.vision_model,
            "embedding_provider": request.embedding_provider,
            "embedding_model": request.embedding_model,
            "embedding_dimensions": request.embedding_dimensions,
            "provenance_fingerprint": provenance.fingerprint,
        }
        fingerprint = image_configuration_fingerprint(configuration)
        repository = ImageSemanticIndexRepository(self.db)
        jobs = JobRepository(self.db)
        existing = await repository.by_fingerprint(media_id, fingerprint)
        if existing is not None:
            job = await jobs.for_image_semantic_index(existing.id)
            if job is None:
                raise RuntimeError("image semantic index exists without processing job")
            enqueue = False
            if job.status == "failed":
                if job.attempt_count >= job.max_attempts:
                    raise ImageSemanticAttemptsExhaustedError(
                        "image semantic index reached its attempt limit"
                    )
                existing.status = "pending"
                existing.error = None
                existing.completed_at = None
                job.status = "pending"
                job.stage = "queued"
                job.progress = 0.0
                job.error = None
                job.completed_at = None
                job.heartbeat_at = None
                enqueue = True
                await self.db.commit()
            return EnsureImageSemanticIndexResult(existing, job, enqueue, False)

        semantic_index = ImageSemanticIndex(
            project_id=media.project_id,
            media_id=media.id,
            status="pending",
            strategy=request.strategy,
            prompt_version=request.prompt_version,
            result_schema_version=request.result_schema_version,
            canonical_text_version=request.canonical_text_version,
            vision_provider=request.vision_provider,
            vision_model=request.vision_model,
            configuration=configuration,
            configuration_fingerprint=fingerprint,
            embedding_provider=request.embedding_provider,
            embedding_model=request.embedding_model,
            embedding_dimensions=request.embedding_dimensions,
        )
        self.db.add(semantic_index)
        try:
            await self.db.flush()
            job = ProcessingJob(
                project_id=media.project_id,
                session_id=None,
                media_id=media.id,
                image_semantic_index_id=semantic_index.id,
                job_type="image_semantic_index",
                status="pending",
                stage="queued",
                progress=0.0,
                max_attempts=self.max_attempts,
            )
            self.db.add(job)
            await self.db.flush()
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            existing = await repository.by_fingerprint(media_id, fingerprint)
            if existing is None:
                raise exc
            existing_job = await jobs.for_image_semantic_index(existing.id)
            if existing_job is None:
                raise RuntimeError(
                    "concurrent image semantic index exists without processing job"
                ) from exc
            return EnsureImageSemanticIndexResult(existing, existing_job, False, False)
        await self.db.refresh(semantic_index)
        await self.db.refresh(job)
        return EnsureImageSemanticIndexResult(semantic_index, job, True, True)


class ImageSemanticIndexProcessor:
    def __init__(
        self,
        db: AsyncSession,
        storage: Storage,
        vision_provider: ImageSemanticProvider,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self.db = db
        self.storage = storage
        self.vision_provider = vision_provider
        self.embedding_provider = embedding_provider

    async def run(self, job: ProcessingJob) -> None:
        if job.image_semantic_index_id is None:
            raise ValueError("image semantic job has no image_semantic_index_id")
        repository = ImageSemanticIndexRepository(self.db)
        semantic_index = await repository.get(job.image_semantic_index_id)
        media = await self.db.get(MediaAsset, job.media_id)
        provenance = await MediaAssetRepository(self.db).provenance(job.media_id)
        if semantic_index is None or media is None or provenance is None:
            raise LookupError("image semantic resources not found")
        if media.media_kind != "image" or not media.content_type:
            raise ImageSemanticPrerequisiteError("semantic target is not a valid source image")
        if semantic_index.vision_provider != self.vision_provider.name:
            raise ImageSemanticPrerequisiteError("configured vision provider is not available")
        if semantic_index.embedding_provider != self.embedding_provider.name:
            raise ImageSemanticPrerequisiteError("configured embedding provider is not available")

        semantic_index.status = "processing"
        semantic_index.started_at = semantic_index.started_at or datetime.now(UTC)
        semantic_index.completed_at = None
        semantic_index.error = None
        job.stage = "describing"
        job.progress = 0.05
        await self.db.commit()

        if semantic_index.semantic_result is None:
            content = await asyncio.to_thread(self.storage.resolve_any(media.storage_uri).read_bytes)
            response = await self.vision_provider.describe(
                content,
                mime_type=media.content_type,
                model=semantic_index.vision_model,
            )
            canonical_text = build_canonical_image_text(provenance, response.result)
            semantic_index.semantic_result = response.result.model_dump(mode="json")
            semantic_index.canonical_search_text = canonical_text
            semantic_index.provider_metadata = {
                **(response.provider_metadata or {}),
                "latency_seconds": response.latency_seconds,
            }
            semantic_index.usage_metadata = response.usage_metadata
            job.stage = "embedding"
            job.progress = 0.65
            job.heartbeat_at = datetime.now(UTC)
            await self.db.commit()
        else:
            result = ImageSemanticResult.model_validate(semantic_index.semantic_result)
            canonical_text = semantic_index.canonical_search_text or build_canonical_image_text(
                provenance, result
            )
            semantic_index.canonical_search_text = canonical_text
            job.stage = "embedding"
            job.progress = 0.65
            await self.db.commit()

        vectors = await self.embedding_provider.embed(
            [canonical_text],
            model=semantic_index.embedding_model,
            expected_dimensions=semantic_index.embedding_dimensions,
        )
        semantic_index.embedding = validate_embeddings(
            vectors,
            expected_count=1,
            expected_dimensions=semantic_index.embedding_dimensions,
        )[0]
        semantic_index.status = "completed"
        semantic_index.completed_at = datetime.now(UTC)
        semantic_index.error = None
        job.stage = "finalizing"
        job.progress = 0.99
        job.heartbeat_at = datetime.now(UTC)
        await self.db.commit()
        logger.info(
            "image_semantic_index_completed",
            project_id=str(semantic_index.project_id),
            media_id=str(media.id),
            image_semantic_index_id=str(semantic_index.id),
            job_id=str(job.id),
            provider_route=semantic_index.vision_model,
            attempt=job.attempt_count,
        )
