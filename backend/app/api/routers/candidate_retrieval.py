from collections.abc import Awaitable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import DB, EmbeddingDep
from app.core.config import settings
from app.repositories.candidate_retrieval import CandidateRetrievalRepository
from app.schemas.candidate_retrieval import (
    CandidateEvidenceOut,
    CandidateFrameOut,
    CandidateHitOut,
    CandidateNoteOut,
    CandidateOptions,
    CandidateSceneOut,
    CandidateTranscriptOut,
    NoteRetrievalCreate,
    QueryCandidateCreate,
    RetrievalCandidateOut,
    RetrievalResultOut,
    WindowTemporalSegmentOut,
)
from app.services.candidate_retrieval import (
    CandidateEvidenceService,
    CandidateRetrievalConfig,
    CandidateRetrievalService,
    RetrievalInProgressError,
    RetrievalPrerequisiteError,
    RetrievalResult,
)
from app.services.embedding_provider import (
    EmbeddingDimensionError,
    EmbeddingProviderError,
)
from app.services.temporal_index import TemporalPrerequisiteError

router = APIRouter(tags=["candidate-retrieval"])


def config_from_payload(payload: CandidateOptions) -> CandidateRetrievalConfig:
    return CandidateRetrievalConfig(
        hit_limit=payload.hit_limit or settings.candidate_hit_limit,
        max_candidates=payload.max_candidates or settings.candidate_max_results,
        min_semantic_score=(
            settings.candidate_min_semantic_score
            if payload.min_semantic_score is None
            else payload.min_semantic_score
        ),
        max_segment_gap_seconds=(
            settings.candidate_max_segment_gap_seconds
            if payload.max_segment_gap_seconds is None
            else payload.max_segment_gap_seconds
        ),
        context_before_seconds=(
            settings.candidate_context_before_seconds
            if payload.context_before_seconds is None
            else payload.context_before_seconds
        ),
        context_after_seconds=(
            settings.candidate_context_after_seconds
            if payload.context_after_seconds is None
            else payload.context_after_seconds
        ),
        max_candidate_duration_seconds=(
            settings.candidate_max_duration_seconds
            if payload.max_candidate_duration_seconds is None
            else payload.max_candidate_duration_seconds
        ),
    )


def result_out(result: RetrievalResult) -> RetrievalResultOut:
    return RetrievalResultOut(
        retrieval_run=result.run,
        candidates=result.candidates,
        created=result.created,
    )


async def execute_retrieval(call: Awaitable[RetrievalResult]) -> RetrievalResultOut:
    try:
        return result_out(await call)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (
        RetrievalPrerequisiteError,
        RetrievalInProgressError,
        TemporalPrerequisiteError,
        EmbeddingDimensionError,
        ValueError,
    ) as exc:
        raise HTTPException(409, str(exc)) from exc
    except EmbeddingProviderError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post(
    "/temporal-indexes/{temporal_index_id}/candidates",
    response_model=RetrievalResultOut,
)
async def retrieve_query_candidates(
    temporal_index_id: UUID,
    payload: QueryCandidateCreate,
    db: DB,
    provider: EmbeddingDep,
) -> RetrievalResultOut:
    return await execute_retrieval(
        CandidateRetrievalService(db, provider).retrieve_query(
            temporal_index_id,
            payload.query,
            config_from_payload(payload),
        )
    )


@router.post(
    "/temporal-indexes/{temporal_index_id}/note-retrievals",
    response_model=RetrievalResultOut,
)
async def retrieve_note_candidates(
    temporal_index_id: UUID,
    payload: NoteRetrievalCreate,
    db: DB,
    provider: EmbeddingDep,
) -> RetrievalResultOut:
    return await execute_retrieval(
        CandidateRetrievalService(db, provider).retrieve_notes(
            temporal_index_id,
            payload.note_ids,
            config_from_payload(payload),
        )
    )


@router.post(
    "/temporal-indexes/{temporal_index_id}/notes/{note_id}/candidates",
    response_model=RetrievalResultOut,
)
async def retrieve_single_note_candidates(
    temporal_index_id: UUID,
    note_id: UUID,
    payload: CandidateOptions,
    db: DB,
    provider: EmbeddingDep,
) -> RetrievalResultOut:
    return await execute_retrieval(
        CandidateRetrievalService(db, provider).retrieve_notes(
            temporal_index_id,
            [note_id],
            config_from_payload(payload),
        )
    )


@router.get(
    "/retrieval-runs/{retrieval_run_id}",
    response_model=RetrievalResultOut,
)
async def get_retrieval_run(
    retrieval_run_id: UUID,
    db: DB,
) -> RetrievalResultOut:
    repository = CandidateRetrievalRepository(db)
    run = await repository.get_run(retrieval_run_id)
    if run is None:
        raise HTTPException(404, "retrieval run not found")
    candidates = list(await repository.candidates(run.id))
    return RetrievalResultOut(
        retrieval_run=run,
        candidates=candidates,
        created=False,
    )


@router.get(
    "/retrieval-runs/{retrieval_run_id}/candidates",
    response_model=list[RetrievalCandidateOut],
)
async def list_retrieval_candidates(
    retrieval_run_id: UUID,
    db: DB,
    note_id: Annotated[UUID | None, Query()] = None,
) -> object:
    repository = CandidateRetrievalRepository(db)
    if await repository.get_run(retrieval_run_id) is None:
        raise HTTPException(404, "retrieval run not found")
    return await repository.candidates(retrieval_run_id, note_id)


@router.get(
    "/retrieval-candidates/{candidate_id}/evidence",
    response_model=CandidateEvidenceOut,
)
async def get_candidate_evidence(
    candidate_id: UUID,
    db: DB,
) -> CandidateEvidenceOut:
    try:
        record = await CandidateEvidenceService(db).get(candidate_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

    frames: list[CandidateFrameOut] = []
    for frame in record.frames:
        assert frame.timestamp_seconds is not None
        frames.append(
            CandidateFrameOut(
                asset_id=frame.id,
                timestamp_seconds=frame.timestamp_seconds,
                storage_uri=frame.storage_uri,
                width=frame.width,
                height=frame.height,
                metadata=frame.asset_metadata,
            )
        )
    return CandidateEvidenceOut(
        candidate=record.candidate,
        retrieval_run=record.run,
        note=(
            CandidateNoteOut(
                id=record.note.id,
                order=record.note.order,
                raw=record.note.raw,
            )
            if record.note
            else None
        ),
        contributing_hits=[
            CandidateHitOut(
                temporal_segment_id=hit.segment.id,
                sequence=hit.segment.sequence,
                start_seconds=hit.segment.start_seconds,
                end_seconds=hit.segment.end_seconds,
                search_text=hit.segment.search_text,
                similarity_score=hit.similarity_score,
                hit_rank=hit.hit_rank,
            )
            for hit in record.contributing_hits
        ],
        temporal_segments=[
            WindowTemporalSegmentOut(
                id=segment.id,
                sequence=segment.sequence,
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
                text=segment.text,
                embedding_status=segment.embedding_status,
            )
            for segment in record.window_segments
        ],
        transcript=[
            CandidateTranscriptOut(
                id=segment.id,
                sequence=segment.sequence,
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
                text=segment.text,
            )
            for segment in record.transcript_segments
        ],
        scenes=[
            CandidateSceneOut(
                id=scene.id,
                sequence=scene.sequence,
                start_seconds=scene.start_seconds,
                end_seconds=scene.end_seconds,
            )
            for scene in record.scenes
        ],
        frames=frames,
    )
