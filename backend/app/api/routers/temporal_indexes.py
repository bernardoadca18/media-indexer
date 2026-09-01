from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import DB, EmbeddingDep
from app.core.config import settings
from app.models import MediaSession
from app.repositories.temporal_index import TemporalIndexRepository
from app.schemas.temporal_index import (
    TemporalIndexCreate,
    TemporalIndexJobOut,
    TemporalIndexOut,
    TemporalSearchIn,
    TemporalSearchItem,
    TemporalSearchOut,
    TemporalSegmentOut,
    TemporalSegmentPage,
)
from app.services.embedding_provider import EmbeddingDimensionError, EmbeddingProviderError
from app.services.temporal_index import (
    TemporalIndexAttemptsExhaustedError,
    TemporalIndexDimensionError,
    TemporalIndexRequest,
    TemporalIndexService,
    TemporalPrerequisiteError,
    TemporalSearchService,
)
from app.workers.runner import job_runner

router = APIRouter(tags=["temporal-indexes"])


@router.post(
    "/sessions/{session_id}/temporal-indexes",
    response_model=TemporalIndexJobOut,
    status_code=202,
)
async def create_temporal_index(
    session_id: UUID,
    payload: TemporalIndexCreate,
    db: DB,
) -> TemporalIndexJobOut:
    request = TemporalIndexRequest(
        transcription_id=payload.transcription_id,
        visual_index_id=payload.visual_index_id,
        target_duration_seconds=(
            settings.temporal_target_duration_seconds
            if payload.target_duration_seconds is None
            else payload.target_duration_seconds
        ),
        min_duration_seconds=(
            settings.temporal_min_duration_seconds
            if payload.min_duration_seconds is None
            else payload.min_duration_seconds
        ),
        max_duration_seconds=(
            settings.temporal_max_duration_seconds
            if payload.max_duration_seconds is None
            else payload.max_duration_seconds
        ),
        embedding_provider="ninerouter",
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
    )
    try:
        result = await TemporalIndexService(db, settings.job_max_attempts).ensure(
            session_id, request
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (
        TemporalPrerequisiteError,
        TemporalIndexAttemptsExhaustedError,
        TemporalIndexDimensionError,
    ) as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.enqueue:
        job_runner.enqueue(result.job.id)
    return TemporalIndexJobOut(
        temporal_index=result.temporal_index,
        job=result.job,
        created=result.created,
    )


@router.get(
    "/sessions/{session_id}/temporal-indexes",
    response_model=list[TemporalIndexOut],
)
async def list_temporal_indexes(session_id: UUID, db: DB) -> object:
    if await db.get(MediaSession, session_id) is None:
        raise HTTPException(404, "session not found")
    return await TemporalIndexRepository(db).list_for_session(session_id)


@router.get("/temporal-indexes/{temporal_index_id}", response_model=TemporalIndexOut)
async def get_temporal_index(temporal_index_id: UUID, db: DB) -> object:
    item = await TemporalIndexRepository(db).get(temporal_index_id)
    if item is None:
        raise HTTPException(404, "temporal index not found")
    return item


@router.get(
    "/temporal-indexes/{temporal_index_id}/segments",
    response_model=TemporalSegmentPage,
)
async def list_temporal_segments(
    temporal_index_id: UUID,
    db: DB,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TemporalSegmentPage:
    repository = TemporalIndexRepository(db)
    if await repository.get(temporal_index_id) is None:
        raise HTTPException(404, "temporal index not found")
    records, total = await repository.segments(temporal_index_id, offset, limit)
    items = [
        TemporalSegmentOut(
            id=record.segment.id,
            temporal_index_id=record.segment.temporal_index_id,
            sequence=record.segment.sequence,
            start_seconds=record.segment.start_seconds,
            end_seconds=record.segment.end_seconds,
            duration_seconds=record.segment.duration_seconds,
            text=record.segment.text,
            search_text=record.segment.search_text,
            metadata=record.segment.segment_metadata,
            embedding_status=record.segment.embedding_status,
            embedding_created_at=record.segment.embedding_created_at,
            transcript_segment_ids=record.transcript_segment_ids,
            scene_segment_ids=record.scene_segment_ids,
            frame_asset_ids=record.frame_asset_ids,
            created_at=record.segment.created_at,
        )
        for record in records
    ]
    return TemporalSegmentPage(items=items, total=total, offset=offset, limit=limit)


@router.post(
    "/temporal-indexes/{temporal_index_id}/search",
    response_model=TemporalSearchOut,
)
async def search_temporal_index(
    temporal_index_id: UUID,
    payload: TemporalSearchIn,
    db: DB,
    provider: EmbeddingDep,
) -> TemporalSearchOut:
    try:
        hits = await TemporalSearchService(db, provider).search(
            temporal_index_id,
            payload.query,
            payload.limit,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (TemporalPrerequisiteError, EmbeddingDimensionError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    except EmbeddingProviderError as exc:
        raise HTTPException(502, str(exc)) from exc
    return TemporalSearchOut(
        items=[
            TemporalSearchItem(
                temporal_segment_id=hit.segment.id,
                start_seconds=hit.segment.start_seconds,
                end_seconds=hit.segment.end_seconds,
                score=hit.score,
            )
            for hit in hits
        ]
    )
