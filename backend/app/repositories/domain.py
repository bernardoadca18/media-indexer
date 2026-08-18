from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import MediaProject, MediaSession, ProcessingJob


class ProjectRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, name: str, description: str | None) -> MediaProject:
        item = MediaProject(name=name, description=description)
        self.db.add(item)
        await self.db.flush()
        return item

    async def get(self, project_id: UUID) -> MediaProject | None:
        return await self.db.get(MediaProject, project_id)

    async def list(self, offset: int, limit: int) -> Sequence[MediaProject]:
        result = await self.db.execute(
            select(MediaProject)
            .order_by(MediaProject.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all()


class SessionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, session_id: UUID) -> MediaSession | None:
        result = await self.db.execute(
            select(MediaSession)
            .options(selectinload(MediaSession.media), selectinload(MediaSession.notes))
            .where(MediaSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def by_idempotency(self, project_id: UUID, key: str) -> MediaSession | None:
        result = await self.db.execute(
            select(MediaSession)
            .options(selectinload(MediaSession.media), selectinload(MediaSession.notes))
            .where(MediaSession.project_id == project_id, MediaSession.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def list_for_project(self, project_id: UUID) -> Sequence[MediaSession]:
        result = await self.db.execute(
            select(MediaSession)
            .where(MediaSession.project_id == project_id)
            .order_by(MediaSession.created_at)
        )
        return result.scalars().all()


class JobRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, job_id: UUID) -> ProcessingJob | None:
        return await self.db.get(ProcessingJob, job_id)

    async def for_session(self, session_id: UUID) -> ProcessingJob | None:
        result = await self.db.execute(
            select(ProcessingJob)
            .where(ProcessingJob.session_id == session_id)
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def pending_ids(self) -> Sequence[UUID]:
        result = await self.db.execute(
            select(ProcessingJob.id)
            .where(ProcessingJob.status == "pending")
            .order_by(ProcessingJob.created_at)
        )
        return result.scalars().all()

    async def for_transcription(self, transcription_id: UUID) -> ProcessingJob | None:
        result = await self.db.execute(
            select(ProcessingJob)
            .where(ProcessingJob.transcription_id == transcription_id)
            .order_by(ProcessingJob.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def recoverable(self, stale_before: datetime) -> Sequence[ProcessingJob]:
        result = await self.db.execute(
            select(ProcessingJob)
            .where(
                or_(
                    ProcessingJob.status == "pending",
                    (
                        (ProcessingJob.status == "processing")
                        & (
                            ProcessingJob.heartbeat_at.is_(None)
                            | (ProcessingJob.heartbeat_at < stale_before)
                        )
                    ),
                )
            )
            .order_by(ProcessingJob.created_at)
        )
        return result.scalars().all()
