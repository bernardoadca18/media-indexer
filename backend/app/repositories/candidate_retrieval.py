from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DerivedAsset,
    Note,
    RetrievalCandidate,
    RetrievalCandidateTemporalSegment,
    RetrievalRun,
    SceneSegment,
    TemporalIndex,
    TemporalSegment,
    TranscriptSegment,
)


@dataclass(frozen=True)
class CandidateHitRecord:
    segment: TemporalSegment
    similarity_score: float
    hit_rank: int


@dataclass(frozen=True)
class CandidateEvidenceRecord:
    candidate: RetrievalCandidate
    run: RetrievalRun
    note: Note | None
    contributing_hits: list[CandidateHitRecord]
    window_segments: list[TemporalSegment]
    transcript_segments: list[TranscriptSegment]
    scenes: list[SceneSegment]
    frames: list[DerivedAsset]


class CandidateRetrievalRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_run(self, retrieval_run_id: UUID) -> RetrievalRun | None:
        return await self.db.get(RetrievalRun, retrieval_run_id)

    async def by_fingerprint(
        self,
        temporal_index_id: UUID,
        fingerprint: str,
    ) -> RetrievalRun | None:
        result = await self.db.execute(
            select(RetrievalRun).where(
                RetrievalRun.temporal_index_id == temporal_index_id,
                RetrievalRun.configuration_fingerprint == fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def candidates(
        self,
        retrieval_run_id: UUID,
        note_id: UUID | None = None,
    ) -> Sequence[RetrievalCandidate]:
        statement = select(RetrievalCandidate).where(
            RetrievalCandidate.retrieval_run_id == retrieval_run_id
        )
        if note_id is not None:
            statement = statement.where(RetrievalCandidate.note_id == note_id)
        result = await self.db.execute(
            statement.order_by(
                RetrievalCandidate.subject_key,
                RetrievalCandidate.rank,
            )
        )
        return result.scalars().all()

    async def clear_candidates(self, retrieval_run_id: UUID) -> None:
        candidate_ids = select(RetrievalCandidate.id).where(
            RetrievalCandidate.retrieval_run_id == retrieval_run_id
        )
        await self.db.execute(
            delete(RetrievalCandidateTemporalSegment).where(
                RetrievalCandidateTemporalSegment.retrieval_candidate_id.in_(candidate_ids)
            )
        )
        await self.db.execute(
            delete(RetrievalCandidate).where(
                RetrievalCandidate.retrieval_run_id == retrieval_run_id
            )
        )

    async def evidence(self, candidate_id: UUID) -> CandidateEvidenceRecord | None:
        candidate = await self.db.get(RetrievalCandidate, candidate_id)
        if candidate is None:
            return None
        run = await self.db.get(RetrievalRun, candidate.retrieval_run_id)
        if run is None:
            return None
        temporal_index = await self.db.get(TemporalIndex, run.temporal_index_id)
        if temporal_index is None:
            return None
        note = await self.db.get(Note, candidate.note_id) if candidate.note_id else None

        hit_rows = await self.db.execute(
            select(
                TemporalSegment,
                RetrievalCandidateTemporalSegment.similarity_score,
                RetrievalCandidateTemporalSegment.hit_rank,
            )
            .join(
                RetrievalCandidateTemporalSegment,
                RetrievalCandidateTemporalSegment.temporal_segment_id == TemporalSegment.id,
            )
            .where(RetrievalCandidateTemporalSegment.retrieval_candidate_id == candidate.id)
            .order_by(RetrievalCandidateTemporalSegment.hit_rank)
        )
        hits = [CandidateHitRecord(row[0], float(row[1]), int(row[2])) for row in hit_rows.all()]
        segment_result = await self.db.execute(
            select(TemporalSegment)
            .where(
                TemporalSegment.temporal_index_id == run.temporal_index_id,
                TemporalSegment.end_seconds > candidate.start_seconds,
                TemporalSegment.start_seconds < candidate.end_seconds,
            )
            .order_by(TemporalSegment.sequence)
        )
        transcript_result = await self.db.execute(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.transcription_id == temporal_index.transcription_id,
                TranscriptSegment.end_seconds > candidate.start_seconds,
                TranscriptSegment.start_seconds < candidate.end_seconds,
            )
            .order_by(TranscriptSegment.sequence)
        )
        scene_result = await self.db.execute(
            select(SceneSegment)
            .where(
                SceneSegment.visual_index_id == temporal_index.visual_index_id,
                SceneSegment.end_seconds > candidate.start_seconds,
                SceneSegment.start_seconds < candidate.end_seconds,
            )
            .order_by(SceneSegment.sequence)
        )
        frame_result = await self.db.execute(
            select(DerivedAsset)
            .where(
                DerivedAsset.visual_index_id == temporal_index.visual_index_id,
                DerivedAsset.kind == "representative_frame",
                DerivedAsset.timestamp_seconds.is_not(None),
                DerivedAsset.timestamp_seconds >= candidate.start_seconds,
                DerivedAsset.timestamp_seconds < candidate.end_seconds,
            )
            .order_by(DerivedAsset.timestamp_seconds, DerivedAsset.id)
        )
        return CandidateEvidenceRecord(
            candidate=candidate,
            run=run,
            note=note,
            contributing_hits=hits,
            window_segments=list(segment_result.scalars().all()),
            transcript_segments=list(transcript_result.scalars().all()),
            scenes=list(scene_result.scalars().all()),
            frames=list(frame_result.scalars().all()),
        )
