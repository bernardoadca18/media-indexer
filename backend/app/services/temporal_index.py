import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EMBEDDING_VECTOR_DIMENSIONS,
    DerivedAsset,
    MediaAsset,
    Note,
    ProcessingJob,
    SceneSegment,
    TemporalIndex,
    TemporalSegment,
    TemporalSegmentFrameLink,
    TemporalSegmentSceneLink,
    TemporalSegmentTranscriptLink,
    Transcription,
    TranscriptSegment,
    VisualIndex,
)
from app.repositories.domain import JobRepository, SessionRepository
from app.repositories.temporal_index import TemporalIndexRepository, TemporalSearchHit
from app.services.embedding_provider import EmbeddingProvider, validate_embeddings

logger = structlog.get_logger(__name__)


class TemporalPrerequisiteError(ValueError):
    pass


class TemporalIndexAttemptsExhaustedError(ValueError):
    pass


class TemporalIndexDimensionError(ValueError):
    pass


@dataclass(frozen=True)
class TemporalIndexRequest:
    transcription_id: UUID
    visual_index_id: UUID
    target_duration_seconds: float
    min_duration_seconds: float
    max_duration_seconds: float
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int


@dataclass(frozen=True)
class EnsureTemporalIndexResult:
    temporal_index: TemporalIndex
    job: ProcessingJob
    enqueue: bool
    created: bool


@dataclass(frozen=True)
class TemporalSegmentDefinition:
    start_seconds: float
    end_seconds: float
    text: str
    search_text: str
    transcript_segment_ids: tuple[UUID, ...]
    scene_segment_ids: tuple[UUID, ...]
    frame_asset_ids: tuple[UUID, ...]
    timeline_note_ids: tuple[UUID, ...] = ()
    semantic_source_types: tuple[str, ...] = ()


TEMPORAL_SEARCH_TEXT_STRATEGY = "asr_human_timeline_v4_chronological"
TEMPORAL_QUERY_SCORING_STRATEGY = "embedding_lexical_evidence_v1"


def _normalize_semantic_text(value: str) -> str:
    return " ".join(value.split())


def _semantic_tokens(value: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(char for char in folded if not unicodedata.combining(char))
    return {token for token in re.findall(r"[a-z0-9]+", ascii_text) if len(token) >= 4}


def hybrid_semantic_score(query: str, evidence: str, embedding_score: float) -> float:
    query_tokens = _semantic_tokens(query)
    evidence_tokens = _semantic_tokens(evidence)
    lexical_coverage = (
        len(query_tokens & evidence_tokens) / len(query_tokens) if query_tokens else 0.0
    )
    return max(0.0, min(1.0, 0.82 * embedding_score + 0.18 * lexical_coverage))


def timeline_note_snapshot_identity(notes: Sequence[Note]) -> str:
    payload = []
    for note in sorted(
        notes,
        key=lambda item: (
            float(item.start_seconds or 0.0),
            float(item.end_seconds or 0.0),
            str(item.id),
        ),
    ):
        if note.start_seconds is None or note.end_seconds is None:
            raise ValueError("timeline note snapshot requires structured temporal ranges")
        payload.append(
            {
                "note_id": str(note.id),
                "start_seconds": float(note.start_seconds),
                "end_seconds": float(note.end_seconds),
                "description": _normalize_semantic_text(note.description or ""),
                "tags": sorted(
                    _normalize_semantic_text(str(tag))
                    for tag in (note.tags or [])
                    if str(tag).strip()
                ),
                "provenance": note.provenance,
            }
        )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def temporal_configuration_fingerprint(config: dict[str, Any]) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _natural_boundary(
    boundaries: Sequence[float],
    *,
    low: float,
    high: float,
    target: float,
) -> float | None:
    candidates = [value for value in boundaries if low <= value <= high]
    if not candidates:
        return None
    return min(candidates, key=lambda value: (abs(value - target), value))


def temporal_boundaries(
    duration_seconds: float,
    transcript_segments: Sequence[TranscriptSegment],
    scenes: Sequence[SceneSegment],
    *,
    target_duration_seconds: float,
    min_duration_seconds: float,
    max_duration_seconds: float,
) -> list[tuple[float, float]]:
    if duration_seconds <= 0:
        raise ValueError("media duration must be positive")
    if not 0 < min_duration_seconds <= target_duration_seconds <= max_duration_seconds:
        raise ValueError("temporal durations must satisfy 0 < min <= target <= max")

    transcript_boundaries = sorted(
        {
            float(segment.end_seconds)
            for segment in transcript_segments
            if 0 < segment.end_seconds < duration_seconds
        }
    )
    scene_boundaries = sorted(
        {float(scene.end_seconds) for scene in scenes if 0 < scene.end_seconds < duration_seconds}
    )
    result: list[tuple[float, float]] = []
    start = 0.0
    epsilon = 0.000001
    while duration_seconds - start > epsilon:
        remaining = duration_seconds - start
        if remaining <= max_duration_seconds and remaining <= (
            target_duration_seconds + min_duration_seconds
        ):
            end = duration_seconds
        else:
            low = start + min_duration_seconds
            high = min(start + max_duration_seconds, duration_seconds)
            target = min(start + target_duration_seconds, high)
            selected = _natural_boundary(
                transcript_boundaries,
                low=low,
                high=high,
                target=target,
            )
            if selected is None:
                selected = _natural_boundary(
                    scene_boundaries,
                    low=low,
                    high=high,
                    target=target,
                )
            if selected is None:
                selected = target
            end = selected
            if duration_seconds - end < min_duration_seconds:
                end = duration_seconds
        if end <= start + epsilon:
            raise ValueError("temporal segmentation did not advance")
        result.append((start, end))
        start = end
    return result


def build_temporal_segments(
    duration_seconds: float,
    transcript_segments: Sequence[TranscriptSegment],
    scenes: Sequence[SceneSegment],
    frames: Sequence[DerivedAsset],
    timeline_notes: Sequence[Note] = (),
    *,
    target_duration_seconds: float,
    min_duration_seconds: float,
    max_duration_seconds: float,
) -> list[TemporalSegmentDefinition]:
    boundaries = temporal_boundaries(
        duration_seconds,
        transcript_segments,
        scenes,
        target_duration_seconds=target_duration_seconds,
        min_duration_seconds=min_duration_seconds,
        max_duration_seconds=max_duration_seconds,
    )
    definitions: list[TemporalSegmentDefinition] = []
    for index, (start, end) in enumerate(boundaries):
        last = index == len(boundaries) - 1
        transcripts = [
            item
            for item in transcript_segments
            if item.end_seconds > start and item.start_seconds < end
        ]
        owned_text = [
            item.text.strip()
            for item in transcript_segments
            if item.start_seconds >= start
            and (item.start_seconds < end or (last and item.start_seconds <= end))
            and item.text.strip()
        ]
        overlapping_scenes = [
            item for item in scenes if item.end_seconds > start and item.start_seconds < end
        ]
        included_frames = [
            item
            for item in frames
            if item.timestamp_seconds is not None
            and item.timestamp_seconds >= start
            and (
                item.timestamp_seconds < end
                or (last and item.timestamp_seconds <= duration_seconds)
            )
        ]
        text = " ".join(owned_text)
        overlapping_notes = [
            note
            for note in timeline_notes
            if note.start_seconds is not None
            and note.end_seconds is not None
            and note.end_seconds > start
            and note.start_seconds < end
            and bool(_normalize_semantic_text(note.description or ""))
        ]
        note_semantics = []
        for note in overlapping_notes:
            description = _normalize_semantic_text(note.description or "")
            tags = " ".join(
                _normalize_semantic_text(str(tag)) for tag in (note.tags or []) if str(tag).strip()
            )
            note_semantics.append(" ".join(part for part in (description, tags) if part))
        search_parts = [text] if text else []
        if note_semantics:
            search_parts.append("Human timeline: " + " ".join(note_semantics))
        source_types = tuple(
            source
            for source, present in (
                ("asr", bool(text)),
                ("human_timeline", bool(note_semantics)),
            )
            if present
        )
        definitions.append(
            TemporalSegmentDefinition(
                start_seconds=start,
                end_seconds=end,
                text=text,
                search_text=" ".join(search_parts),
                transcript_segment_ids=tuple(item.id for item in transcripts),
                scene_segment_ids=tuple(item.id for item in overlapping_scenes),
                frame_asset_ids=tuple(item.id for item in included_frames),
                timeline_note_ids=tuple(note.id for note in overlapping_notes),
                semantic_source_types=source_types,
            )
        )
    return definitions


class TemporalIndexService:
    def __init__(self, db: AsyncSession, max_attempts: int):
        self.db = db
        self.max_attempts = max_attempts

    async def ensure(
        self,
        session_id: UUID,
        request: TemporalIndexRequest,
    ) -> EnsureTemporalIndexResult:
        session = await SessionRepository(self.db).get(session_id)
        if session is None:
            raise LookupError("session not found")
        if session.status != "ready":
            raise TemporalPrerequisiteError("session must be ready before temporal indexing")
        media_id = session.media_id
        transcription = await self.db.get(Transcription, request.transcription_id)
        visual_index = await self.db.get(VisualIndex, request.visual_index_id)
        if transcription is None or transcription.session_id != session_id:
            raise TemporalPrerequisiteError("transcription does not belong to the session")
        if visual_index is None or visual_index.session_id != session_id:
            raise TemporalPrerequisiteError("visual index does not belong to the session")
        if transcription.media_id != media_id or visual_index.media_id != media_id:
            raise TemporalPrerequisiteError("temporal prerequisites reference different media")
        if transcription.status != "completed":
            raise TemporalPrerequisiteError("transcription must be completed")
        if visual_index.status != "completed":
            raise TemporalPrerequisiteError("visual index must be completed")
        if request.embedding_dimensions != EMBEDDING_VECTOR_DIMENSIONS:
            raise TemporalIndexDimensionError(
                f"database vector dimension is {EMBEDDING_VECTOR_DIMENSIONS}; "
                f"configured {request.embedding_dimensions}"
            )
        if not (
            0
            < request.min_duration_seconds
            <= request.target_duration_seconds
            <= request.max_duration_seconds
        ):
            raise TemporalPrerequisiteError("durations must satisfy 0 < min <= target <= max")

        config: dict[str, Any] = {
            "transcription_id": str(request.transcription_id),
            "visual_index_id": str(request.visual_index_id),
            "target_duration_seconds": request.target_duration_seconds,
            "min_duration_seconds": request.min_duration_seconds,
            "max_duration_seconds": request.max_duration_seconds,
            "boundary_strategy": "transcript_end_then_scene_end",
            "text_ownership": "transcript_start",
            "interval_semantics": "[start,end)",
            "embedding_provider": request.embedding_provider,
            "embedding_model": request.embedding_model,
            "embedding_dimensions": request.embedding_dimensions,
            "similarity_metric": "cosine",
            "semantic_text_strategy": TEMPORAL_SEARCH_TEXT_STRATEGY,
        }
        indexes = TemporalIndexRepository(self.db)
        eligible_notes = await indexes.eligible_timeline_notes(session_id)
        config["note_snapshot_identity"] = timeline_note_snapshot_identity(eligible_notes)
        fingerprint = temporal_configuration_fingerprint(config)
        jobs = JobRepository(self.db)
        existing = await indexes.by_fingerprint(media_id, fingerprint)
        if existing is not None:
            job = await jobs.for_temporal_index(existing.id)
            if job is None:
                raise RuntimeError("temporal index exists without processing job")
            enqueue = False
            if job.status == "failed":
                if job.attempt_count >= job.max_attempts:
                    raise TemporalIndexAttemptsExhaustedError(
                        "temporal index reached its attempt limit"
                    )
                existing.status = "pending"
                existing.started_at = None
                existing.completed_at = None
                job.status = "pending"
                job.stage = "queued"
                job.progress = 0.0
                job.error = None
                job.completed_at = None
                job.heartbeat_at = None
                enqueue = True
                await self.db.commit()
            return EnsureTemporalIndexResult(existing, job, enqueue, False)

        temporal_index = TemporalIndex(
            project_id=session.project_id,
            session_id=session.id,
            media_id=media_id,
            transcription_id=request.transcription_id,
            visual_index_id=request.visual_index_id,
            status="pending",
            configuration=config,
            configuration_fingerprint=fingerprint,
            embedding_provider=request.embedding_provider,
            embedding_model=request.embedding_model,
            embedding_dimensions=request.embedding_dimensions,
        )
        self.db.add(temporal_index)
        try:
            await self.db.flush()
            job = ProcessingJob(
                project_id=session.project_id,
                session_id=session.id,
                media_id=media_id,
                temporal_index_id=temporal_index.id,
                job_type="temporal_index",
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
            existing = await indexes.by_fingerprint(media_id, fingerprint)
            if existing is None:
                raise exc
            existing_job = await jobs.for_temporal_index(existing.id)
            if existing_job is None:
                raise RuntimeError("concurrent temporal index has no processing job") from exc
            return EnsureTemporalIndexResult(existing, existing_job, False, False)
        await self.db.refresh(temporal_index)
        await self.db.refresh(job)
        return EnsureTemporalIndexResult(temporal_index, job, True, True)


class TemporalIndexProcessor:
    def __init__(
        self,
        db: AsyncSession,
        embedding_provider: EmbeddingProvider,
        batch_size: int,
    ):
        self.db = db
        self.embedding_provider = embedding_provider
        self.batch_size = max(1, batch_size)

    async def run(self, job: ProcessingJob) -> None:
        if job.temporal_index_id is None:
            raise ValueError("temporal index job has no temporal_index_id")
        repository = TemporalIndexRepository(self.db)
        temporal_index = await repository.get(job.temporal_index_id)
        if temporal_index is None:
            raise LookupError("temporal index not found")
        transcription = await self.db.get(Transcription, temporal_index.transcription_id)
        visual_index = await self.db.get(VisualIndex, temporal_index.visual_index_id)
        media = await self.db.get(MediaAsset, temporal_index.media_id)
        if transcription is None or visual_index is None or media is None:
            raise LookupError("temporal index prerequisites not found")
        if transcription.status != "completed" or visual_index.status != "completed":
            raise TemporalPrerequisiteError("temporal index prerequisites are not completed")
        if media.duration_seconds is None or media.duration_seconds <= 0:
            raise TemporalPrerequisiteError("media duration is required")
        if temporal_index.embedding_provider != self.embedding_provider.name:
            raise TemporalPrerequisiteError("configured embedding provider is not available")
        if temporal_index.embedding_dimensions != EMBEDDING_VECTOR_DIMENSIONS:
            raise TemporalIndexDimensionError(
                "temporal index vector dimension does not match schema"
            )
        temporal_index_id = temporal_index.id
        log_ids = {
            "project_id": str(job.project_id),
            "session_id": str(job.session_id),
            "media_id": str(job.media_id),
            "temporal_index_id": str(temporal_index_id),
            "job_id": str(job.id),
        }
        current_stage = "building_segments"

        await repository.clear_segments(temporal_index.id)
        temporal_index.status = "processing"
        temporal_index.started_at = datetime.now(UTC)
        temporal_index.completed_at = None
        job.stage = "building_segments"
        job.progress = 0.05
        await self.db.commit()

        segment_count = 0
        embedded_count = 0
        try:
            transcripts = await repository.transcript_segments(transcription.id)
            scenes = await repository.scenes(visual_index.id)
            frames = await repository.frames(visual_index.id)
            timeline_notes = await repository.eligible_timeline_notes(temporal_index.session_id)
            configuration = temporal_index.configuration
            configured_strategy = configuration.get("semantic_text_strategy")
            if configured_strategy == TEMPORAL_SEARCH_TEXT_STRATEGY:
                actual_snapshot = timeline_note_snapshot_identity(timeline_notes)
                if actual_snapshot != configuration.get("note_snapshot_identity"):
                    raise TemporalPrerequisiteError(
                        "eligible timeline notes changed after temporal index creation"
                    )
            else:
                timeline_notes = []
            definitions = build_temporal_segments(
                media.duration_seconds,
                transcripts,
                scenes,
                frames,
                timeline_notes,
                target_duration_seconds=float(configuration["target_duration_seconds"]),
                min_duration_seconds=float(configuration["min_duration_seconds"]),
                max_duration_seconds=float(configuration["max_duration_seconds"]),
            )
            for sequence, definition in enumerate(definitions, start=1):
                silent = not definition.search_text.strip()
                segment = TemporalSegment(
                    temporal_index_id=temporal_index.id,
                    sequence=sequence,
                    start_seconds=definition.start_seconds,
                    end_seconds=definition.end_seconds,
                    duration_seconds=definition.end_seconds - definition.start_seconds,
                    text=definition.text,
                    search_text=definition.search_text,
                    segment_metadata={
                        "silent": silent,
                        "asr_silent": not bool(definition.text.strip()),
                        "interval_semantics": "[start,end)",
                        "text_ownership": "transcript_start",
                        "semantic_text_strategy": configured_strategy or "asr_only_v1",
                        "semantic_source_types": list(definition.semantic_source_types),
                        "overlapping_timeline_note_ids": [
                            str(note_id) for note_id in definition.timeline_note_ids
                        ],
                    },
                    embedding=None,
                    embedding_status="not_applicable" if silent else "pending",
                )
                self.db.add(segment)
                await self.db.flush()
                self.db.add_all(
                    [
                        TemporalSegmentTranscriptLink(
                            temporal_segment_id=segment.id,
                            transcript_segment_id=evidence_id,
                        )
                        for evidence_id in definition.transcript_segment_ids
                    ]
                )
                self.db.add_all(
                    [
                        TemporalSegmentSceneLink(
                            temporal_segment_id=segment.id,
                            scene_segment_id=evidence_id,
                        )
                        for evidence_id in definition.scene_segment_ids
                    ]
                )
                self.db.add_all(
                    [
                        TemporalSegmentFrameLink(
                            temporal_segment_id=segment.id,
                            derived_asset_id=evidence_id,
                        )
                        for evidence_id in definition.frame_asset_ids
                    ]
                )
                segment_count = sequence
                if sequence % self.batch_size == 0:
                    job.heartbeat_at = datetime.now(UTC)
                    job.progress = 0.05 + 0.4 * sequence / max(1, len(definitions))
                    await self.db.commit()
            await self.db.commit()

            current_stage = "embedding_segments"
            job.stage = "embedding_segments"
            job.progress = 0.5
            await self.db.commit()
            while True:
                result = await self.db.execute(
                    select(TemporalSegment)
                    .where(
                        TemporalSegment.temporal_index_id == temporal_index.id,
                        TemporalSegment.embedding_status == "pending",
                    )
                    .order_by(TemporalSegment.sequence)
                    .limit(self.batch_size)
                )
                batch = list(result.scalars().all())
                if not batch:
                    break
                vectors = await self.embedding_provider.embed(
                    [segment.search_text for segment in batch],
                    model=temporal_index.embedding_model,
                    expected_dimensions=temporal_index.embedding_dimensions,
                )
                validate_embeddings(
                    vectors,
                    expected_count=len(batch),
                    expected_dimensions=temporal_index.embedding_dimensions,
                )
                for segment, vector in zip(batch, vectors, strict=True):
                    segment.embedding = vector
                    segment.embedding_status = "completed"
                    segment.embedding_created_at = datetime.now(UTC)
                    embedded_count += 1
                job.heartbeat_at = datetime.now(UTC)
                job.progress = 0.5 + 0.45 * embedded_count / max(1, segment_count)
                await self.db.commit()

            current_stage = "finalizing"
            temporal_index.status = "completed"
            temporal_index.completed_at = datetime.now(UTC)
            job.stage = "finalizing"
            job.progress = 0.99
            job.heartbeat_at = datetime.now(UTC)
            await self.db.commit()
            logger.info(
                "temporal_index_completed",
                project_id=str(job.project_id),
                session_id=str(job.session_id),
                media_id=str(job.media_id),
                temporal_index_id=str(temporal_index.id),
                transcription_id=str(transcription.id),
                visual_index_id=str(visual_index.id),
                job_id=str(job.id),
                segment_count=segment_count,
                embedded_segment_count=embedded_count,
                embedding_model=temporal_index.embedding_model,
                stage="finalizing",
            )
        except Exception:
            await self.db.rollback()
            temporal_index = await repository.get(temporal_index_id)
            if temporal_index is not None:
                await repository.clear_segments(temporal_index.id)
                temporal_index.status = "failed"
                temporal_index.completed_at = datetime.now(UTC)
                await self.db.commit()
            logger.exception(
                "temporal_index_failed",
                **log_ids,
                segment_count=segment_count,
                embedded_segment_count=embedded_count,
                stage=current_stage,
            )
            raise


class TemporalSearchService:
    def __init__(
        self,
        db: AsyncSession,
        embedding_provider: EmbeddingProvider,
    ):
        self.db = db
        self.embedding_provider = embedding_provider

    async def search(
        self,
        temporal_index_id: UUID,
        query: str,
        limit: int,
    ) -> list[TemporalSearchHit]:
        results = await self.search_many(temporal_index_id, [query], limit)
        return results[0]

    async def search_many(
        self,
        temporal_index_id: UUID,
        queries: Sequence[str],
        limit: int,
    ) -> list[list[TemporalSearchHit]]:
        if any(not query.strip() for query in queries):
            raise ValueError("search query must not be blank")
        repository = TemporalIndexRepository(self.db)
        temporal_index = await repository.get(temporal_index_id)
        if temporal_index is None:
            raise LookupError("temporal index not found")
        if temporal_index.status != "completed":
            raise TemporalPrerequisiteError("temporal index must be completed before search")
        if temporal_index.embedding_provider != self.embedding_provider.name:
            raise TemporalPrerequisiteError("configured embedding provider is not available")
        if not queries:
            return []
        vectors = await self.embedding_provider.embed(
            list(queries),
            model=temporal_index.embedding_model,
            expected_dimensions=temporal_index.embedding_dimensions,
        )
        validate_embeddings(
            vectors,
            expected_count=len(queries),
            expected_dimensions=temporal_index.embedding_dimensions,
        )
        result: list[list[TemporalSearchHit]] = []
        pool_limit = min(200, max(limit, limit * 3))
        for query, vector in zip(queries, vectors, strict=True):
            hits = await repository.search(temporal_index.id, vector, pool_limit)
            reranked = [
                TemporalSearchHit(
                    segment=hit.segment,
                    score=hybrid_semantic_score(query, hit.segment.search_text, hit.score),
                )
                for hit in hits
            ]
            reranked.sort(
                key=lambda hit: (-hit.score, hit.segment.sequence, str(hit.segment.id))
            )
            result.append(reranked[:limit])
        return result
