from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DerivedAsset, SceneSegment, VisualIndex


class VisualIndexRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, visual_index_id: UUID) -> VisualIndex | None:
        return await self.db.get(VisualIndex, visual_index_id)

    async def by_fingerprint(self, media_id: UUID, fingerprint: str) -> VisualIndex | None:
        result = await self.db.execute(
            select(VisualIndex).where(
                VisualIndex.media_id == media_id,
                VisualIndex.configuration_fingerprint == fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_session(self, session_id: UUID) -> Sequence[VisualIndex]:
        result = await self.db.execute(
            select(VisualIndex)
            .where(VisualIndex.session_id == session_id)
            .order_by(VisualIndex.created_at.desc())
        )
        return result.scalars().all()

    async def scenes(
        self, visual_index_id: UUID, offset: int, limit: int
    ) -> tuple[Sequence[SceneSegment], int]:
        total = await self.db.scalar(
            select(func.count(SceneSegment.id)).where(
                SceneSegment.visual_index_id == visual_index_id
            )
        )
        result = await self.db.execute(
            select(SceneSegment)
            .where(SceneSegment.visual_index_id == visual_index_id)
            .order_by(SceneSegment.sequence)
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all(), int(total or 0)

    async def frames(
        self, visual_index_id: UUID, offset: int, limit: int
    ) -> tuple[Sequence[DerivedAsset], int]:
        condition = (DerivedAsset.visual_index_id == visual_index_id) & (
            DerivedAsset.kind == "representative_frame"
        )
        total = await self.db.scalar(select(func.count(DerivedAsset.id)).where(condition))
        result = await self.db.execute(
            select(DerivedAsset)
            .where(condition)
            .order_by(DerivedAsset.timestamp_seconds, DerivedAsset.created_at)
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all(), int(total or 0)

    async def clear_outputs(self, visual_index_id: UUID) -> None:
        await self.db.execute(
            delete(DerivedAsset).where(DerivedAsset.visual_index_id == visual_index_id)
        )
        await self.db.execute(
            delete(SceneSegment).where(SceneSegment.visual_index_id == visual_index_id)
        )
