from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import DB
from app.core.config import settings
from app.models import MediaSession
from app.repositories.alignment import AlignmentRepository
from app.schemas.alignment import (
    AlignmentAcceptedOut,
    AlignmentCreate,
    AlignmentRunDetailOut,
    AlignmentRunOut,
    CandidateAlignmentOut,
    CandidateAlignmentPageOut,
)
from app.services.alignment import (
    AlignmentAttemptsExhaustedError,
    AlignmentConfig,
    AlignmentPrerequisiteError,
    AlignmentRequest,
    AlignmentService,
)
from app.services.alignment_provider import LiteLLMMultimodalAlignmentProvider
from app.workers.runner import job_runner

router = APIRouter(tags=["multimodal-alignment"])


def _config(payload: AlignmentCreate) -> AlignmentConfig:
    return AlignmentConfig(
        frame_interval_seconds=payload.frame_interval_seconds
        or settings.alignment_frame_interval_seconds,
        max_frames=payload.max_frames or settings.alignment_max_frames,
        frame_width=payload.frame_width or settings.alignment_frame_width,
        jpeg_quality=payload.jpeg_quality or settings.alignment_jpeg_quality,
        frame_dedup_tolerance_seconds=(settings.alignment_frame_dedup_tolerance_seconds),
        max_transcript_characters=payload.max_transcript_characters
        or settings.alignment_max_transcript_characters,
        refinement_enabled=(
            settings.alignment_refinement_enabled
            if payload.refinement_enabled is None
            else payload.refinement_enabled
        ),
        refinement_frame_interval_seconds=(settings.alignment_refinement_frame_interval_seconds),
        refinement_padding_seconds=settings.alignment_refinement_padding_seconds,
        refinement_max_frames=settings.alignment_refinement_max_frames,
        early_stop_confidence=(
            settings.alignment_early_stop_confidence
            if payload.early_stop_confidence is None
            else payload.early_stop_confidence
        ),
    )


@router.post(
    "/retrieval-runs/{retrieval_run_id}/alignments",
    response_model=AlignmentAcceptedOut,
    status_code=202,
)
async def create_alignment(
    retrieval_run_id: UUID,
    payload: AlignmentCreate,
    db: DB,
) -> AlignmentAcceptedOut:
    request = AlignmentRequest(
        model=payload.model or settings.alignment_model,
        candidate_ids=(tuple(payload.candidate_ids) if payload.candidate_ids is not None else None),
        config=_config(payload),
    )
    try:
        result = await AlignmentService(
            db,
            provider_name=LiteLLMMultimodalAlignmentProvider.name,
            max_attempts=settings.job_max_attempts,
        ).ensure(retrieval_run_id, request)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (
        AlignmentPrerequisiteError,
        AlignmentAttemptsExhaustedError,
        ValueError,
    ) as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.enqueue:
        job_runner.enqueue(result.job.id)
    return AlignmentAcceptedOut(
        alignment_run=result.alignment_run,
        job=result.job,
        created=result.created,
    )


@router.get(
    "/alignment-runs/{alignment_run_id}",
    response_model=AlignmentRunDetailOut,
)
async def get_alignment_run(
    alignment_run_id: UUID,
    db: DB,
) -> AlignmentRunDetailOut:
    repository = AlignmentRepository(db)
    run = await repository.get_run(alignment_run_id)
    if run is None:
        raise HTTPException(404, "alignment run not found")
    return AlignmentRunDetailOut(
        alignment_run=run,
        subjects=list(await repository.subjects(run.id)),
    )


@router.get(
    "/sessions/{session_id}/alignment-runs",
    response_model=list[AlignmentRunOut],
)
async def list_session_alignment_runs(
    session_id: UUID,
    db: DB,
    status: Annotated[str | None, Query()] = None,
) -> list[AlignmentRunOut]:
    """List AlignmentRuns for a session, optionally filtered by status.

    Most recent first. This supports discovery of compatible alignment runs
    for semantic timeline retrieval.
    """
    # Verify session exists
    session = await db.get(MediaSession, session_id)
    if session is None:
        raise HTTPException(404, "session not found")

    repository = AlignmentRepository(db)
    runs = await repository.list_runs_by_session(session_id, status=status)
    return [AlignmentRunOut.model_validate(run) for run in runs]


@router.get(
    "/alignment-runs/{alignment_run_id}/results",
    response_model=CandidateAlignmentPageOut,
)
async def list_alignment_results(
    alignment_run_id: UUID,
    db: DB,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> CandidateAlignmentPageOut:
    repository = AlignmentRepository(db)
    if await repository.get_run(alignment_run_id) is None:
        raise HTTPException(404, "alignment run not found")
    items, total = await repository.results(alignment_run_id, offset, limit)
    return CandidateAlignmentPageOut(
        items=list(items),
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/candidate-alignments/{candidate_alignment_id}",
    response_model=CandidateAlignmentOut,
)
async def get_candidate_alignment(
    candidate_alignment_id: UUID,
    db: DB,
) -> object:
    result = await AlignmentRepository(db).result(candidate_alignment_id)
    if result is None:
        raise HTTPException(404, "candidate alignment not found")
    return result
