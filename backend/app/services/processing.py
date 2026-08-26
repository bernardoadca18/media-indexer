import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaAsset, MediaSession, ProcessingJob
from app.services.probe import FFprobe
from app.services.storage import Storage

logger = structlog.get_logger(__name__)


class InitialIndexProcessor:
    def __init__(self, db: AsyncSession, storage: Storage, probe: FFprobe):
        self.db, self.storage, self.probe = db, storage, probe

    async def run(self, job: ProcessingJob) -> None:
        job_id = job.id
        media_id = job.media_id
        session_id = job.session_id
        job.stage, job.progress = "ffprobe", 0.25
        await self.db.commit()
        try:
            media = await self.db.get(MediaAsset, media_id)
            session = await self.db.get(MediaSession, session_id)
            assert media is not None and session is not None
            result = await self.probe.inspect(self.storage.resolve(media.storage_uri))
            media.duration_seconds = result.duration_seconds
            media.format_name = result.format_name
            media.video_codec = result.video_codec
            media.audio_codec = result.audio_codec
            media.fps, media.width, media.height = result.fps, result.width, result.height
            media.probe_metadata = result.raw
            session.status = "ready"
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            session = await self.db.get(MediaSession, session_id)
            if session is not None:
                session.status = "failed"
                await self.db.commit()
            logger.exception("initial_index_failed", job_id=str(job_id))
            raise
