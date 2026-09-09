from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DerivedAsset,
    Note,
    SceneSegment,
    TemporalIndex,
    TemporalSegment,
    TemporalSegmentFrameLink,
    TemporalSegmentSceneLink,
    TemporalSegmentTranscriptLink,
    TranscriptSegment,
)


@dataclass(frozen=True)
class TemporalSegmentEvidence:
    segment: TemporalSegment
    transcript_segment_ids: list[UUID]
    scene_segment_ids: list[UUID]
    frame_asset_ids: list[UUID]


@dataclass(frozen=True)
class TemporalSearchHit:
    segment: TemporalSegment
    score: float


class TemporalIndexRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, temporal_index_id: UUID) -> TemporalIndex | None:
        return await self.db.get(TemporalIndex, temporal_index_id)

    async def by_fingerprint(self, media_id: UUID, fingerprint: str) -> TemporalIndex | None:
        result = await self.db.execute(
            select(TemporalIndex).where(
                TemporalIndex.media_id == media_id,
                TemporalIndex.configuration_fingerprint == fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_session(self, session_id: UUID) -> Sequence[TemporalIndex]:
        result = await self.db.execute(
            select(TemporalIndex)
            .where(TemporalIndex.session_id == session_id)
            .order_by(TemporalIndex.created_at.desc())
        )
        return result.scalars().all()

    async def transcript_segments(self, transcription_id: UUID) -> Sequence[TranscriptSegment]:
        result = await self.db.execute(
            select(TranscriptSegment)
            .where(TranscriptSegment.transcription_id == transcription_id)
            .order_by(TranscriptSegment.sequence)
        )
        return result.scalars().all()

    async def scenes(self, visual_index_id: UUID) -> Sequence[SceneSegment]:
        result = await self.db.execute(
            select(SceneSegment)
            .where(SceneSegment.visual_index_id == visual_index_id)
            .order_by(SceneSegment.sequence)
        )
        return result.scalars().all()

    async def frames(self, visual_index_id: UUID) -> Sequence[DerivedAsset]:
        result = await self.db.execute(
            select(DerivedAsset)
            .where(
                DerivedAsset.visual_index_id == visual_index_id,
                DerivedAsset.kind == "representative_frame",
                DerivedAsset.timestamp_seconds.is_not(None),
            )
            .order_by(DerivedAsset.timestamp_seconds, DerivedAsset.created_at)
        )
        return result.scalars().all()

    async def eligible_timeline_notes(self, session_id: UUID) -> Sequence[Note]:
        result = await self.db.execute(
            select(Note)
            .where(
                Note.session_id == session_id,
                Note.start_seconds.is_not(None),
                Note.end_seconds.is_not(None),
                Note.end_seconds > Note.start_seconds,
                func.length(func.trim(Note.description)) > 0,
            )
            .order_by(
                Note.start_seconds,
                Note.end_seconds,
                Note.order,
                Note.id,
            )
        )
        return result.scalars().all()

    async def clear_segments(self, temporal_index_id: UUID) -> None:
        segment_ids = select(TemporalSegment.id).where(
            TemporalSegment.temporal_index_id == temporal_index_id
        )
        await self.db.execute(
            delete(TemporalSegmentTranscriptLink).where(
                TemporalSegmentTranscriptLink.temporal_segment_id.in_(segment_ids)
            )
        )
        await self.db.execute(
            delete(TemporalSegmentSceneLink).where(
                TemporalSegmentSceneLink.temporal_segment_id.in_(segment_ids)
            )
        )
        await self.db.execute(
            delete(TemporalSegmentFrameLink).where(
                TemporalSegmentFrameLink.temporal_segment_id.in_(segment_ids)
            )
        )
        await self.db.execute(
            delete(TemporalSegment).where(TemporalSegment.temporal_index_id == temporal_index_id)
        )

    async def segments(
        self, temporal_index_id: UUID, offset: int, limit: int
    ) -> tuple[list[TemporalSegmentEvidence], int]:
        total = int(
            await self.db.scalar(
                select(func.count(TemporalSegment.id)).where(
                    TemporalSegment.temporal_index_id == temporal_index_id
                )
            )
            or 0
        )
        result = await self.db.execute(
            select(TemporalSegment)
            .where(TemporalSegment.temporal_index_id == temporal_index_id)
            .order_by(TemporalSegment.sequence)
            .offset(offset)
            .limit(limit)
        )
        segments = list(result.scalars().all())
        if not segments:
            return [], total
        segment_ids = [segment.id for segment in segments]
        transcript_map: dict[UUID, list[UUID]] = {item: [] for item in segment_ids}
        scene_map: dict[UUID, list[UUID]] = {item: [] for item in segment_ids}
        frame_map: dict[UUID, list[UUID]] = {item: [] for item in segment_ids}

        transcript_rows = await self.db.execute(
            select(
                TemporalSegmentTranscriptLink.temporal_segment_id,
                TemporalSegmentTranscriptLink.transcript_segment_id,
            )
            .join(
                TranscriptSegment,
                TranscriptSegment.id == TemporalSegmentTranscriptLink.transcript_segment_id,
            )
            .where(TemporalSegmentTranscriptLink.temporal_segment_id.in_(segment_ids))
            .order_by(TemporalSegmentTranscriptLink.temporal_segment_id, TranscriptSegment.sequence)
        )
        for segment_id, evidence_id in transcript_rows:
            transcript_map[segment_id].append(evidence_id)

        scene_rows = await self.db.execute(
            select(
                TemporalSegmentSceneLink.temporal_segment_id,
                TemporalSegmentSceneLink.scene_segment_id,
            )
            .join(SceneSegment, SceneSegment.id == TemporalSegmentSceneLink.scene_segment_id)
            .where(TemporalSegmentSceneLink.temporal_segment_id.in_(segment_ids))
            .order_by(TemporalSegmentSceneLink.temporal_segment_id, SceneSegment.sequence)
        )
        for segment_id, evidence_id in scene_rows:
            scene_map[segment_id].append(evidence_id)

        frame_rows = await self.db.execute(
            select(
                TemporalSegmentFrameLink.temporal_segment_id,
                TemporalSegmentFrameLink.derived_asset_id,
            )
            .join(DerivedAsset, DerivedAsset.id == TemporalSegmentFrameLink.derived_asset_id)
            .where(TemporalSegmentFrameLink.temporal_segment_id.in_(segment_ids))
            .order_by(
                TemporalSegmentFrameLink.temporal_segment_id,
                DerivedAsset.timestamp_seconds,
                DerivedAsset.id,
            )
        )
        for segment_id, evidence_id in frame_rows:
            frame_map[segment_id].append(evidence_id)

        return [
            TemporalSegmentEvidence(
                segment,
                transcript_map[segment.id],
                scene_map[segment.id],
                frame_map[segment.id],
            )
            for segment in segments
        ], total

    async def search(
        self,
        temporal_index_id: UUID,
        query_embedding: list[float],
        limit: int,
    ) -> list[TemporalSearchHit]:
        embedding_column = TemporalSegment.__table__.c.embedding
        distance = embedding_column.cosine_distance(query_embedding)
        result = await self.db.execute(
            select(TemporalSegment, (1.0 - distance).label("score"))
            .where(
                TemporalSegment.temporal_index_id == temporal_index_id,
                TemporalSegment.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(limit)
        )
        return [TemporalSearchHit(segment=row[0], score=float(row[1])) for row in result.all()]
