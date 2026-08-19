from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from app.models import (
    AlignmentRun,
    AlignmentSubject,
    CandidateAlignment,
    DerivedAsset,
    MediaSession,
    Note,
    RetrievalRun,
    SceneSegment,
    TemporalIndex,
    TemporalSegment,
    TemporalSegmentFrameLink,
    TemporalSegmentSceneLink,
    TemporalSegmentTranscriptLink,
    TranscriptSegment,
)


@dataclass(frozen=True)
class TimelineContext:
    session: MediaSession
    temporal_index: TemporalIndex


@dataclass(frozen=True)
class TimelineEvidenceMaps:
    transcripts: dict[UUID, list[TranscriptSegment]]
    scenes: dict[UUID, list[SceneSegment]]
    frames: dict[UUID, list[DerivedAsset]]


@dataclass(frozen=True)
class TimelineAlignmentContext:
    alignment_run: AlignmentRun
    retrieval_run: RetrievalRun


@dataclass(frozen=True)
class TimelineNoteSelection:
    subject: AlignmentSubject
    selected: CandidateAlignment | None


@dataclass(frozen=True)
class TimelineSelectedOverlap:
    candidate_alignment_id: UUID
    note_id: UUID
    temporal_segment_id: UUID


class SemanticTimelineRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def context(
        self,
        session_id: UUID,
        temporal_index_id: UUID,
    ) -> TimelineContext | None:
        row = (
            await self.db.execute(
                select(MediaSession, TemporalIndex)
                .join(
                    TemporalIndex,
                    TemporalIndex.session_id == MediaSession.id,
                )
                .where(
                    MediaSession.id == session_id,
                    TemporalIndex.id == temporal_index_id,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        return TimelineContext(row[0], row[1])

    async def alignment_context(
        self,
        alignment_run_id: UUID,
    ) -> TimelineAlignmentContext | None:
        row = (
            await self.db.execute(
                select(AlignmentRun, RetrievalRun)
                .join(
                    RetrievalRun,
                    RetrievalRun.id == AlignmentRun.retrieval_run_id,
                )
                .where(AlignmentRun.id == alignment_run_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return TimelineAlignmentContext(row[0], row[1])

    async def segments(
        self,
        temporal_index_id: UUID,
        *,
        start_seconds: float | None,
        end_seconds: float | None,
        offset: int,
        limit: int,
    ) -> tuple[list[TemporalSegment], int]:
        filters = [TemporalSegment.temporal_index_id == temporal_index_id]
        if start_seconds is not None:
            filters.append(TemporalSegment.end_seconds > start_seconds)
        if end_seconds is not None:
            filters.append(TemporalSegment.start_seconds < end_seconds)
        total = int(
            await self.db.scalar(select(func.count(TemporalSegment.id)).where(*filters)) or 0
        )
        rows = await self.db.execute(
            select(TemporalSegment)
            .options(
                load_only(
                    TemporalSegment.id,
                    TemporalSegment.temporal_index_id,
                    TemporalSegment.sequence,
                    TemporalSegment.start_seconds,
                    TemporalSegment.end_seconds,
                    TemporalSegment.duration_seconds,
                    TemporalSegment.text,
                    TemporalSegment.segment_metadata,
                )
            )
            .where(*filters)
            .order_by(TemporalSegment.sequence, TemporalSegment.id)
            .offset(offset)
            .limit(limit)
        )
        return list(rows.scalars().all()), total

    async def evidence(
        self,
        segment_ids: Sequence[UUID],
    ) -> TimelineEvidenceMaps:
        transcripts: dict[UUID, list[TranscriptSegment]] = {
            segment_id: [] for segment_id in segment_ids
        }
        scenes: dict[UUID, list[SceneSegment]] = {segment_id: [] for segment_id in segment_ids}
        frames: dict[UUID, list[DerivedAsset]] = {segment_id: [] for segment_id in segment_ids}
        if not segment_ids:
            return TimelineEvidenceMaps(transcripts, scenes, frames)

        transcript_rows = await self.db.execute(
            select(
                TemporalSegmentTranscriptLink.temporal_segment_id,
                TranscriptSegment,
            )
            .options(
                load_only(
                    TranscriptSegment.id,
                    TranscriptSegment.sequence,
                    TranscriptSegment.start_seconds,
                    TranscriptSegment.end_seconds,
                )
            )
            .join(
                TranscriptSegment,
                TranscriptSegment.id == TemporalSegmentTranscriptLink.transcript_segment_id,
            )
            .where(TemporalSegmentTranscriptLink.temporal_segment_id.in_(segment_ids))
            .order_by(
                TemporalSegmentTranscriptLink.temporal_segment_id,
                TranscriptSegment.sequence,
                TranscriptSegment.id,
            )
        )
        for segment_id, transcript in transcript_rows:
            transcripts[segment_id].append(transcript)

        scene_rows = await self.db.execute(
            select(TemporalSegmentSceneLink.temporal_segment_id, SceneSegment)
            .options(
                load_only(
                    SceneSegment.id,
                    SceneSegment.sequence,
                    SceneSegment.start_seconds,
                    SceneSegment.end_seconds,
                )
            )
            .join(
                SceneSegment,
                SceneSegment.id == TemporalSegmentSceneLink.scene_segment_id,
            )
            .where(TemporalSegmentSceneLink.temporal_segment_id.in_(segment_ids))
            .order_by(
                TemporalSegmentSceneLink.temporal_segment_id,
                SceneSegment.sequence,
                SceneSegment.id,
            )
        )
        for segment_id, scene in scene_rows:
            scenes[segment_id].append(scene)

        frame_rows = await self.db.execute(
            select(TemporalSegmentFrameLink.temporal_segment_id, DerivedAsset)
            .options(
                load_only(
                    DerivedAsset.id,
                    DerivedAsset.kind,
                    DerivedAsset.storage_uri,
                    DerivedAsset.mime_type,
                    DerivedAsset.width,
                    DerivedAsset.height,
                    DerivedAsset.timestamp_seconds,
                    DerivedAsset.asset_metadata,
                )
            )
            .join(
                DerivedAsset,
                DerivedAsset.id == TemporalSegmentFrameLink.derived_asset_id,
            )
            .where(TemporalSegmentFrameLink.temporal_segment_id.in_(segment_ids))
            .order_by(
                TemporalSegmentFrameLink.temporal_segment_id,
                DerivedAsset.timestamp_seconds,
                DerivedAsset.id,
            )
        )
        for segment_id, frame in frame_rows:
            frames[segment_id].append(frame)
        return TimelineEvidenceMaps(transcripts, scenes, frames)

    async def notes(self, session_id: UUID) -> Sequence[Note]:
        rows = await self.db.execute(
            select(Note).where(Note.session_id == session_id).order_by(Note.order, Note.id)
        )
        return rows.scalars().all()

    async def note_selections(
        self,
        alignment_run_id: UUID,
    ) -> Sequence[TimelineNoteSelection]:
        rows = await self.db.execute(
            select(AlignmentSubject, CandidateAlignment)
            .outerjoin(
                CandidateAlignment,
                CandidateAlignment.id == AlignmentSubject.selected_candidate_alignment_id,
            )
            .where(
                AlignmentSubject.alignment_run_id == alignment_run_id,
                AlignmentSubject.note_id.is_not(None),
            )
            .order_by(AlignmentSubject.subject_key, AlignmentSubject.id)
        )
        return [TimelineNoteSelection(subject=row[0], selected=row[1]) for row in rows.all()]

    async def selected_overlaps(
        self,
        temporal_index_id: UUID,
        alignment_run_id: UUID,
    ) -> Sequence[TimelineSelectedOverlap]:
        rows = await self.db.execute(
            select(
                CandidateAlignment.id,
                AlignmentSubject.note_id,
                TemporalSegment.id,
            )
            .join(
                AlignmentSubject,
                AlignmentSubject.selected_candidate_alignment_id == CandidateAlignment.id,
            )
            .join(
                TemporalSegment,
                and_(
                    TemporalSegment.temporal_index_id == temporal_index_id,
                    TemporalSegment.end_seconds > CandidateAlignment.start_seconds,
                    TemporalSegment.start_seconds < CandidateAlignment.end_seconds,
                ),
            )
            .where(
                AlignmentSubject.alignment_run_id == alignment_run_id,
                AlignmentSubject.note_id.is_not(None),
                CandidateAlignment.status == "matched",
            )
            .order_by(
                CandidateAlignment.id,
                TemporalSegment.sequence,
                TemporalSegment.id,
            )
        )
        return [
            TimelineSelectedOverlap(
                candidate_alignment_id=row[0],
                note_id=row[1],
                temporal_segment_id=row[2],
            )
            for row in rows.all()
        ]
