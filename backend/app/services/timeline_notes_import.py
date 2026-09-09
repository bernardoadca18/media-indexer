"""Shared persistence boundary for structured human timeline observations."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import MediaSession, Note
from app.schemas.domain import TimelineNoteObservation


@dataclass(frozen=True, slots=True)
class TimelineNotesImportSummary:
    imported_count: int
    skipped_count: int
    errors: tuple[str, ...]


class TimelineNotesImporter:
    """Import structured observations without mutating existing raw notes."""

    DURATION_TOLERANCE_SECONDS = 0.001

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def import_observations(
        self,
        session_id: UUID,
        observations: list[TimelineNoteObservation],
        *,
        provenance: str,
    ) -> TimelineNotesImportSummary:
        result = await self.db.execute(
            select(MediaSession)
            .where(MediaSession.id == session_id)
            .options(selectinload(MediaSession.media))
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise LookupError("session not found")
        media_duration = session.media.duration_seconds if session.media else None

        max_order = (
            await self.db.scalar(
                select(Note.order)
                .where(Note.session_id == session_id)
                .order_by(Note.order.desc())
                .limit(1)
            )
            or 0
        )
        imported = 0
        skipped = 0
        errors: list[str] = []
        for index, observation in enumerate(observations):
            start = float(observation.start_seconds)
            end = float(observation.end_seconds)
            description = observation.description.strip()
            if start < 0:
                errors.append(f"Observation {index}: start_seconds must be non-negative")
                continue
            if end <= start:
                errors.append(f"Observation {index}: end_seconds must be greater than start_seconds")
                continue
            if (
                media_duration is not None
                and end > media_duration + self.DURATION_TOLERANCE_SECONDS
            ):
                errors.append(
                    f"Observation {index}: end_seconds ({end}) exceeds media duration "
                    f"({media_duration})"
                )
                continue

            duplicate = await self.db.scalar(
                select(Note.id)
                .where(Note.session_id == session_id)
                .where(Note.start_seconds == start)
                .where(Note.end_seconds == end)
                .where(func.lower(func.trim(Note.description)) == description.lower())
                .limit(1)
            )
            if duplicate is not None:
                skipped += 1
                continue

            max_order += 1
            self.db.add(
                Note(
                    session_id=session_id,
                    order=max_order,
                    raw=observation.description,
                    start_seconds=start,
                    end_seconds=end,
                    description=observation.description,
                    tags=observation.tags,
                    provenance=provenance,
                )
            )
            imported += 1

        await self.db.flush()
        return TimelineNotesImportSummary(imported, skipped, tuple(errors))
