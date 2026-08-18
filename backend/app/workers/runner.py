import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services.jobs import JobExecutor, ProcessorRegistry
from app.services.probe import FFprobe
from app.services.processing import InitialIndexProcessor
from app.services.storage import LocalStorage
from app.services.transcription import TranscriptionProcessor
from app.services.transcription_provider import FasterWhisperProvider

logger = structlog.get_logger(__name__)


def build_default_registry() -> ProcessorRegistry:
    storage = LocalStorage(settings.storage_root)
    provider = FasterWhisperProvider(
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )
    registry = ProcessorRegistry()
    registry.register(
        "initial_index",
        lambda db: InitialIndexProcessor(db, storage, FFprobe(settings.ffprobe_binary)),
    )
    registry.register(
        "transcription",
        lambda db: TranscriptionProcessor(
            db,
            storage,
            provider,
            settings.transcription_batch_size,
        ),
    )
    return registry


class JobRunner:
    def __init__(
        self,
        registry: ProcessorRegistry | None = None,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
        stale_timeout_seconds: int = settings.job_stale_timeout_seconds,
        heartbeat_interval_seconds: int = settings.job_heartbeat_interval_seconds,
    ) -> None:
        self.registry = registry or build_default_registry()
        self.session_factory = session_factory
        self.stale_timeout_seconds = stale_timeout_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.queue: asyncio.Queue[UUID] = asyncio.Queue()
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        cutoff = datetime.now(UTC) - timedelta(seconds=self.stale_timeout_seconds)
        async with self.session_factory() as db:
            recovered = await JobExecutor(db, self.registry).recover(cutoff)
        for job_id in recovered:
            self.queue.put_nowait(job_id)
        self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    def enqueue(self, job_id: UUID) -> None:
        self.queue.put_nowait(job_id)

    async def _heartbeat(self, job_id: UUID) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval_seconds)
            async with self.session_factory() as db:
                active = await JobExecutor(db, self.registry).heartbeat(job_id)
            if not active:
                return

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            heartbeat = asyncio.create_task(self._heartbeat(job_id))
            try:
                async with self.session_factory() as db:
                    await JobExecutor(db, self.registry).execute(job_id)
            except Exception:
                logger.exception("job_runner_error", job_id=str(job_id))
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
                self.queue.task_done()


job_runner = JobRunner()
