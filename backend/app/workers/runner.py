import asyncio
from uuid import UUID

import structlog

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.repositories.domain import JobRepository
from app.services.probe import FFprobe
from app.services.processing import InitialIndexProcessor
from app.services.storage import LocalStorage

logger = structlog.get_logger(__name__)


class JobRunner:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[UUID] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        async with AsyncSessionLocal() as db:
            for job_id in await JobRepository(db).pending_ids():
                self.queue.put_nowait(job_id)
        self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def enqueue(self, job_id: UUID) -> None:
        self.queue.put_nowait(job_id)

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                async with AsyncSessionLocal() as db:
                    processor = InitialIndexProcessor(
                        db, LocalStorage(settings.storage_root), FFprobe(settings.ffprobe_binary)
                    )
                    await processor.run(job_id)
            except Exception:
                logger.warning("job_processing_failed", job_id=str(job_id))
            finally:
                self.queue.task_done()


job_runner = JobRunner()
