from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaSession, ProcessingJob, Transcription
from app.repositories.domain import JobRepository

logger = structlog.get_logger(__name__)


class JobProcessor(Protocol):
    async def run(self, job: ProcessingJob) -> None: ...


ProcessorFactory = Callable[[AsyncSession], JobProcessor]


class UnknownJobTypeError(LookupError):
    pass


class ProcessorRegistry:
    def __init__(self, factories: dict[str, ProcessorFactory] | None = None):
        self._factories = factories or {}

    def register(self, job_type: str, factory: ProcessorFactory) -> None:
        self._factories[job_type] = factory

    def create(self, job_type: str, db: AsyncSession) -> JobProcessor:
        factory = self._factories.get(job_type)
        if factory is None:
            raise UnknownJobTypeError(f"unknown job type: {job_type}")
        return factory(db)


class JobExecutor:
    def __init__(self, db: AsyncSession, registry: ProcessorRegistry):
        self.db = db
        self.registry = registry

    async def execute(self, job_id: UUID) -> bool:
        job = await JobRepository(self.db).get(job_id)
        if job is None or job.status != "pending":
            return False
        if job.attempt_count >= job.max_attempts:
            await self._fail(job, "attempt limit reached")
            return False

        now = datetime.now(UTC)
        job.status = "processing"
        job.stage = "starting"
        job.attempt_count += 1
        job.started_at = job.started_at or now
        job.completed_at = None
        job.heartbeat_at = now
        job.error = None
        await self.db.commit()

        try:
            processor = self.registry.create(job.job_type, self.db)
            await processor.run(job)
            job.status = "completed"
            job.stage = "completed"
            job.progress = 1.0
            job.heartbeat_at = datetime.now(UTC)
            job.completed_at = datetime.now(UTC)
            await self.db.commit()
            logger.info(
                "job_completed",
                job_id=str(job.id),
                job_type=job.job_type,
                attempt=job.attempt_count,
            )
            return True
        except Exception as exc:
            await self.db.rollback()
            job = await JobRepository(self.db).get(job_id)
            if job is not None:
                await self._fail(job, str(exc)[:4000])
            logger.exception("job_failed", job_id=str(job_id))
            return False

    async def heartbeat(self, job_id: UUID) -> bool:
        job = await JobRepository(self.db).get(job_id)
        if job is None or job.status != "processing":
            return False
        job.heartbeat_at = datetime.now(UTC)
        await self.db.commit()
        return True

    async def recover(self, stale_before: datetime) -> Sequence[UUID]:
        jobs = await JobRepository(self.db).recoverable(stale_before)
        recovered: list[UUID] = []
        for job in jobs:
            stale = job.status == "processing"
            if stale and job.attempt_count >= job.max_attempts:
                await self._fail(job, "stale job reached attempt limit", commit=False)
                continue
            if job.status == "pending" and job.attempt_count >= job.max_attempts:
                await self._fail(job, "attempt limit reached", commit=False)
                continue
            if stale:
                job.status = "pending"
                job.stage = "recovered"
                job.progress = 0.0
                job.error = None
                job.completed_at = None
                job.heartbeat_at = None
                if job.transcription_id is not None:
                    transcription = await self.db.get(Transcription, job.transcription_id)
                    if transcription is not None:
                        transcription.status = "pending"
                        transcription.completed_at = None
            recovered.append(job.id)
        await self.db.commit()
        return recovered

    async def _fail(self, job: ProcessingJob, error: str, *, commit: bool = True) -> None:
        job.status = "failed"
        job.stage = "failed"
        job.error = error
        job.completed_at = datetime.now(UTC)
        job.heartbeat_at = datetime.now(UTC)
        if job.transcription_id is not None:
            transcription = await self.db.get(Transcription, job.transcription_id)
            if transcription is not None:
                transcription.status = "failed"
                transcription.completed_at = datetime.now(UTC)
        elif job.job_type == "initial_index":
            session = await self.db.get(MediaSession, job.session_id)
            if session is not None:
                session.status = "failed"
        if commit:
            await self.db.commit()
