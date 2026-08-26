from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AlignmentRun,
    AlignmentSubject,
    CandidateAlignment,
    DerivedAsset,
    RetrievalCandidate,
    RetrievalRun,
)


class AlignmentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_run(self, alignment_run_id: UUID) -> AlignmentRun | None:
        return await self.db.get(AlignmentRun, alignment_run_id)

    async def by_fingerprint(
        self,
        retrieval_run_id: UUID,
        fingerprint: str,
    ) -> AlignmentRun | None:
        result = await self.db.execute(
            select(AlignmentRun).where(
                AlignmentRun.retrieval_run_id == retrieval_run_id,
                AlignmentRun.configuration_fingerprint == fingerprint,
            )
        )
        return result.scalar_one_or_none()

    async def retrieval_run(self, retrieval_run_id: UUID) -> RetrievalRun | None:
        return await self.db.get(RetrievalRun, retrieval_run_id)

    async def candidates(
        self,
        retrieval_run_id: UUID,
        candidate_ids: set[UUID] | None = None,
    ) -> Sequence[RetrievalCandidate]:
        statement = select(RetrievalCandidate).where(
            RetrievalCandidate.retrieval_run_id == retrieval_run_id
        )
        if candidate_ids is not None:
            statement = statement.where(RetrievalCandidate.id.in_(candidate_ids))
        result = await self.db.execute(
            statement.order_by(
                RetrievalCandidate.subject_key,
                RetrievalCandidate.rank,
            )
        )
        return result.scalars().all()

    async def subjects(self, alignment_run_id: UUID) -> Sequence[AlignmentSubject]:
        result = await self.db.execute(
            select(AlignmentSubject)
            .where(AlignmentSubject.alignment_run_id == alignment_run_id)
            .order_by(AlignmentSubject.subject_key)
        )
        return result.scalars().all()

    async def results(
        self,
        alignment_run_id: UUID,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[Sequence[CandidateAlignment], int]:
        statement = select(CandidateAlignment).where(
            CandidateAlignment.alignment_run_id == alignment_run_id
        )
        total = await self.db.scalar(select(func.count()).select_from(statement.subquery()))
        result = await self.db.execute(
            statement.order_by(
                CandidateAlignment.alignment_subject_id,
                CandidateAlignment.retrieval_rank,
            )
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all(), int(total or 0)

    async def result(self, candidate_alignment_id: UUID) -> CandidateAlignment | None:
        return await self.db.get(CandidateAlignment, candidate_alignment_id)

    async def clear_outputs(self, alignment_run_id: UUID) -> None:
        await self.db.execute(
            update(AlignmentSubject)
            .where(AlignmentSubject.alignment_run_id == alignment_run_id)
            .values(selected_candidate_alignment_id=None)
        )
        await self.db.execute(
            delete(CandidateAlignment).where(
                CandidateAlignment.alignment_run_id == alignment_run_id
            )
        )
        await self.db.execute(
            delete(AlignmentSubject).where(AlignmentSubject.alignment_run_id == alignment_run_id)
        )
        await self.db.execute(
            delete(DerivedAsset).where(DerivedAsset.alignment_run_id == alignment_run_id)
        )

    async def alignment_assets(
        self,
        alignment_run_id: UUID,
    ) -> Sequence[DerivedAsset]:
        result = await self.db.execute(
            select(DerivedAsset)
            .where(DerivedAsset.alignment_run_id == alignment_run_id)
            .order_by(DerivedAsset.timestamp_seconds, DerivedAsset.id)
        )
        return result.scalars().all()

    async def list_runs_by_session(
        self,
        session_id: UUID,
        status: str | None = None,
    ) -> Sequence[AlignmentRun]:
        """List AlignmentRuns for a session, optionally filtered by status.

        Returns runs ordered by created_at descending (most recent first).
        """
        statement = select(AlignmentRun).where(AlignmentRun.session_id == session_id)
        if status:
            statement = statement.where(AlignmentRun.status == status)
        result = await self.db.execute(statement.order_by(AlignmentRun.created_at.desc()))
        return result.scalars().all()
