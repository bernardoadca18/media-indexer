from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from app.api.dependencies import DB
from app.core.config import settings
from app.models import MediaSession
from app.repositories.transcription import TranscriptionRepository
from app.schemas.transcription import (
    TranscriptionCreate,
    TranscriptionJobOut,
    TranscriptionOut,
    TranscriptSegmentPage,
)
from app.services.transcription import (
    SessionNotReadyError,
    TranscriptionAttemptsExhaustedError,
    TranscriptionRequest,
    TranscriptionService,
)
from app.workers.runner import job_runner

router = APIRouter(tags=["transcriptions"])


@router.post(
    "/sessions/{session_id}/transcriptions",
    response_model=TranscriptionJobOut,
    status_code=202,
)
async def create_transcription(
    session_id: UUID, payload: TranscriptionCreate, db: DB
) -> TranscriptionJobOut:
    configured_language = (
        payload.language if payload.language is not None else settings.whisper_language
    )
    language = configured_language.strip() or None
    request = TranscriptionRequest(
        model=payload.model or settings.whisper_model,
        language=language,
        beam_size=payload.beam_size or settings.whisper_beam_size,
        vad_filter=(
            settings.whisper_vad_filter if payload.vad_filter is None else payload.vad_filter
        ),
        word_timestamps=(
            settings.whisper_word_timestamps
            if payload.word_timestamps is None
            else payload.word_timestamps
        ),
        compute_type=settings.whisper_compute_type,
    )
    try:
        result = await TranscriptionService(db, settings.job_max_attempts).ensure(
            session_id, request
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except SessionNotReadyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except TranscriptionAttemptsExhaustedError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result.enqueue:
        job_runner.enqueue(result.job.id)
    return TranscriptionJobOut(
        transcription=result.transcription,
        job=result.job,
        created=result.created,
    )


@router.get(
    "/sessions/{session_id}/transcriptions",
    response_model=list[TranscriptionOut],
)
async def list_transcriptions(session_id: UUID, db: DB) -> object:
    if await db.get(MediaSession, session_id) is None:
        raise HTTPException(404, "session not found")
    return await TranscriptionRepository(db).list_for_session(session_id)


@router.get(
    "/transcriptions/{transcription_id}",
    response_model=TranscriptionOut,
)
async def get_transcription(transcription_id: UUID, db: DB) -> object:
    item = await TranscriptionRepository(db).get(transcription_id)
    if item is None:
        raise HTTPException(404, "transcription not found")
    return item


@router.get(
    "/transcriptions/{transcription_id}/segments",
    response_model=TranscriptSegmentPage,
)
async def list_segments(
    transcription_id: UUID,
    db: DB,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> TranscriptSegmentPage:
    repository = TranscriptionRepository(db)
    if await repository.get(transcription_id) is None:
        raise HTTPException(404, "transcription not found")
    items, total = await repository.segments(transcription_id, offset, limit)
    return TranscriptSegmentPage(items=list(items), total=total, offset=offset, limit=limit)
