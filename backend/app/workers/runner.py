import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services.alignment import AlignmentProcessor
from app.services.alignment_provider import LiteLLMMultimodalAlignmentProvider
from app.services.embedding_provider import LiteLLMEmbeddingProvider
from app.services.jobs import JobExecutor, ProcessorRegistry
from app.services.probe import FFprobe
from app.services.processing import InitialIndexProcessor
from app.services.storage import LocalStorage
from app.services.temporal_index import TemporalIndexProcessor
from app.services.transcription import TranscriptionProcessor
from app.services.transcription_provider import FasterWhisperProvider
from app.services.visual_index import VisualIndexProcessor
from app.services.visual_tools import FFmpegFrameExtractor, FFmpegSceneDetector

logger = structlog.get_logger(__name__)


def build_default_registry() -> ProcessorRegistry:
    storage = LocalStorage(settings.storage_root)
    transcription_provider = FasterWhisperProvider(
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
        model_lifetime=settings.whisper_model_lifetime,
    )
    scene_detector = FFmpegSceneDetector(settings.ffmpeg_binary)
    frame_extractor = FFmpegFrameExtractor(settings.ffmpeg_binary)
    embedding_provider = LiteLLMEmbeddingProvider(
        settings.litellm_base_url,
        settings.litellm_api_key,
        settings.embedding_timeout_seconds,
    )
    alignment_provider = LiteLLMMultimodalAlignmentProvider(
        settings.litellm_base_url,
        settings.litellm_api_key,
        settings.alignment_timeout_seconds,
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
            transcription_provider,
            settings.transcription_batch_size,
        ),
    )
    registry.register(
        "visual_index",
        lambda db: VisualIndexProcessor(
            db,
            storage,
            scene_detector,
            frame_extractor,
            settings.visual_index_batch_size,
        ),
    )
    registry.register(
        "temporal_index",
        lambda db: TemporalIndexProcessor(
            db,
            embedding_provider,
            settings.embedding_batch_size,
        ),
    )
    registry.register(
        "multimodal_alignment",
        lambda db: AlignmentProcessor(
            db,
            storage,
            frame_extractor,
            alignment_provider,
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

    @property
    def operational(self) -> bool:
        return self.task is not None and not self.task.done()

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
