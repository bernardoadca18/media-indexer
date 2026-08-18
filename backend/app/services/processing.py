from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaAsset, MediaSession
from app.repositories.domain import JobRepository
from app.services.probe import FFprobe
from app.services.storage import Storage

logger = structlog.get_logger(__name__)


class InitialIndexProcessor:
    def __init__(self, db: AsyncSession, storage: Storage, probe: FFprobe):
        self.db, self.storage, self.probe = db, storage, probe

    async def run(self, job_id: UUID) -> None:
        repo = JobRepository(self.db)
        job = await repo.get(job_id)
        if job is None or job.status == "completed":
            return
        job.status, job.stage, job.progress = "processing", "ffprobe", 0.25
        job.started_at, job.error = datetime.now(UTC), None
        await self.db.commit()
        try:
            media = await self.db.get(MediaAsset, job.media_id)
            session = await self.db.get(MediaSession, job.session_id)
            assert media is not None and session is not None
            result = await self.probe.inspect(self.storage.resolve(media.storage_uri))
            media.duration_seconds = result.duration_seconds
            media.format_name = result.format_name
            media.video_codec = result.video_codec
            media.audio_codec = result.audio_codec
            media.fps, media.width, media.height = result.fps, result.width, result.height
            media.probe_metadata = result.raw
            session.status = "ready"
            job.status, job.stage, job.progress = "completed", "completed", 1.0
            job.completed_at = datetime.now(UTC)
            await self.db.commit()
            logger.info("job_completed", job_id=str(job.id), session_id=str(job.session_id))
        except Exception as exc:
            await self.db.rollback()
            job = await repo.get(job_id)
            if job is not None:
                session = await self.db.get(MediaSession, job.session_id)
                job.status, job.stage, job.error = "failed", "ffprobe", str(exc)[:4000]
                job.completed_at = datetime.now(UTC)
                if session is not None:
                    session.status = "failed"
                await self.db.commit()
            logger.exception("job_failed", job_id=str(job_id))
            raise
