from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.dependencies import DB
from app.models import MediaSession
from app.schemas.domain import (
    TimelineNotesImportRequest,
    TimelineNotesImportResult,
)
from sqlalchemy import select

router = APIRouter(tags=["timeline-notes"])


@router.post(
    "/sessions/{session_id}/timeline-notes/import",
    response_model=TimelineNotesImportResult,
)
async def import_timeline_notes(
    session_id: UUID,
    request: TimelineNotesImportRequest,
    db: DB,
) -> TimelineNotesImportResult:
    """
    Bulk import structured timeline notes with temporal provenance.

    Observations are imported with their explicit temporal ranges, creating
    Note records with start_seconds, end_seconds, description, and tags.

    Idempotency: Duplicates are detected by matching (session_id, start_seconds, end_seconds).
    """
    # Load session with media for duration validation
    result = await db.execute(
        select(MediaSession).where(MediaSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(404, "Session not found")

    # Get media duration for validation
    media_duration = session.media.duration_seconds if session.media else None

    imported_count = 0
    skipped_count = 0
    errors: list[str] = []

    # Get the max order for new notes
    from app.models.entities import Note

    order_result = await db.execute(
        select(Note.order)
        .where(Note.session_id == session_id)
        .order_by(Note.order.desc())
        .limit(1)
    )
    max_order = order_result.scalar() or 0

    for i, obs in enumerate(request.observations):
        # Validate against media duration if available
        if media_duration is not None:
            if obs.end_seconds > media_duration:
                errors.append(
                    f"Observation {i}: end_seconds ({obs.end_seconds}) exceeds media duration ({media_duration})"
                )
                continue

        # Check for existing note with same range (idempotency)
        existing_result = await db.execute(
            select(Note)
            .where(Note.session_id == session_id)
            .where(Note.start_seconds == obs.start_seconds)
            .where(Note.end_seconds == obs.end_seconds)
        )
        if existing_result.scalar_one_or_none() is not None:
            skipped_count += 1
            continue

        # Create new note
        max_order += 1
        note = Note(
            session_id=session_id,
            order=max_order,
            raw=obs.description,  # Store description in raw for now
            start_seconds=obs.start_seconds,
            end_seconds=obs.end_seconds,
            description=obs.description,
            tags=obs.tags,
        )
        db.add(note)
        imported_count += 1

    await db.flush()

    return TimelineNotesImportResult(
        imported_count=imported_count,
        skipped_count=skipped_count,
        errors=errors if errors else None,
    )
