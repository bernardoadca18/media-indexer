from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transcription, TranscriptSegment


class TranscriptionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, transcription_id: UUID) -> Transcription | None:
        return await self.db.get(Transcription, transcription_id)

    async def by_fingerprint(self, media_id: UUID, fingerprint: str) -> Transcription | None:
        result = await self.db.execute(
            select(Transcription).where(
                Transcription.media_id == media_id,
                Transcription.configuration_fingerprint == fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_session(self, session_id: UUID) -> Sequence[Transcription]:
        result = await self.db.execute(
            select(Transcription)
            .where(Transcription.session_id == session_id)
            .order_by(Transcription.created_at.desc())
        )
        return result.scalars().all()

    async def segments(
        self, transcription_id: UUID, offset: int, limit: int
    ) -> tuple[Sequence[TranscriptSegment], int]:
        total = await self.db.scalar(
            select(func.count(TranscriptSegment.id)).where(
                TranscriptSegment.transcription_id == transcription_id
            )
        )
        result = await self.db.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.transcription_id == transcription_id)
            .order_by(TranscriptSegment.sequence)
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all(), int(total or 0)

    async def clear_segments(self, transcription_id: UUID) -> None:
        await self.db.execute(
            delete(TranscriptSegment).where(TranscriptSegment.transcription_id == transcription_id)
        )
