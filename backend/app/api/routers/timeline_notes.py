from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.dependencies import DB
from app.models import MediaAsset, MediaSession
from app.schemas.domain import (
    TimelineNotesImportRequest,
    TimelineNotesImportResult,
    TimelineNotesTextImportRequest,
    TimelineNotesTextImportResult,
    TimelineTextInvalidLine,
)
from app.services.timeline_note_text_parser import parse_timeline_text
from app.services.timeline_notes_import import TimelineNotesImporter

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
    Note records with start_seconds, end_seconds, description, tags, and provenance.

    Idempotency: Duplicates are detected by matching (session_id, start_seconds, end_seconds, normalized_description).
    This allows the same temporal range to have different descriptions (different observations).
    """
    try:
        summary = await TimelineNotesImporter(db).import_observations(
            session_id,
            request.observations,
            provenance=request.provenance or "manual_import",
        )
    except LookupError:
        raise HTTPException(404, "Session not found") from None
    await db.commit()
    return TimelineNotesImportResult(
        imported_count=summary.imported_count,
        skipped_count=summary.skipped_count,
        errors=list(summary.errors) or None,
    )


@router.post(
    "/sessions/{session_id}/timeline-notes/import-text",
    response_model=TimelineNotesTextImportResult,
)
async def import_timeline_note_text(
    session_id: UUID,
    request: TimelineNotesTextImportRequest,
    db: DB,
) -> TimelineNotesTextImportResult:
    session = await db.get(MediaSession, session_id)
    if session is None:
        raise HTTPException(404, "Session not found")
    duration = await db.scalar(
        select(MediaAsset.duration_seconds)
        .join(MediaSession, MediaSession.media_id == MediaAsset.id)
        .where(MediaSession.id == session_id)
    )
    # The importer performs authoritative media-duration validation. Parsing also
    # reports line-level format/range failures; it never guesses missing ranges.
    parsed = parse_timeline_text(request.content, media_duration_seconds=duration)
    summary = await TimelineNotesImporter(db).import_observations(
        session_id,
        list(parsed.observations),
        provenance=request.provenance,
    )
    await db.commit()
    invalid = [
        TimelineTextInvalidLine(
            line_number=item.line_number,
            line=item.line,
            reason=item.reason,
        )
        for item in parsed.invalid_lines
    ]
    invalid.extend(
        TimelineTextInvalidLine(line_number=0, line="", reason=error)
        for error in summary.errors
    )
    return TimelineNotesTextImportResult(
        parsed_count=len(parsed.observations),
        imported_count=summary.imported_count,
        skipped_count=summary.skipped_count,
        invalid_lines=invalid,
        ignored_line_count=parsed.ignored_line_count,
    )
