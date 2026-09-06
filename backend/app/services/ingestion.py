from dataclasses import dataclass
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import MediaAsset, MediaSession, Note, ProcessingJob
from app.repositories.domain import JobRepository, ProjectRepository, SessionRepository
from app.services.note_parser import split_note_units
from app.services.storage import Storage


class ProjectNotFoundError(LookupError):
    pass


class InvalidNotesError(ValueError):
    pass


@dataclass(frozen=True)
class IngestionResult:
    session: MediaSession
    job: ProcessingJob
    created: bool


class IngestionService:
    def __init__(self, db: AsyncSession, storage: Storage):
        self.db = db
        self.storage = storage

    async def ingest(
        self,
        project_id: UUID,
        label: str,
        video: UploadFile,
        notes: UploadFile | None = None,
        idempotency_key: str | None = None,
    ) -> IngestionResult:
        projects = ProjectRepository(self.db)
        sessions = SessionRepository(self.db)
        jobs = JobRepository(self.db)
        if await projects.get(project_id) is None:
            raise ProjectNotFoundError

        if idempotency_key:
            existing = await sessions.by_idempotency(project_id, idempotency_key)
            if existing:
                job = await jobs.for_session(existing.id)
                assert job is not None
                return IngestionResult(existing, job, False)

        # Handle optional notes file
        raw_notes: str | None = None
        notes_filename: str | None = None
        if notes is not None:
            raw_bytes = await notes.read()
            try:
                raw_notes = raw_bytes.decode("utf-8")
                notes_filename = notes.filename or "notes.txt"
            except UnicodeDecodeError as exc:
                raise InvalidNotesError("notes must be UTF-8") from exc

        session_id, media_id = uuid4(), uuid4()
        # Use HDD storage for large files if configured
        video_size = video.size if hasattr(video, "size") else None
        if video_size is not None and video_size >= settings.storage_hdd_threshold_bytes:
            stored = await self.storage.save_source_hdd(project_id, session_id, video)
        else:
            stored = await self.storage.save_source(project_id, session_id, video)
        try:
            media = MediaAsset(
                id=media_id,
                project_id=project_id,
                storage_uri=stored.uri,
                original_filename=video.filename or "source.bin",
                content_type=video.content_type,
                byte_size=stored.byte_size,
                sha256=stored.sha256,
            )
            session = MediaSession(
                id=session_id,
                project_id=project_id,
                media_id=media_id,
                label=label,
                notes_filename=notes_filename,
                raw_notes=raw_notes,
                idempotency_key=idempotency_key,
                status="pending",
            )
            self.db.add_all([media, session])
            # Establish FK targets before adding notes/jobs that only carry IDs.
            # This also gives PostgreSQL deterministic flush ordering.
            await self.db.flush()
            # Only create Note entities if notes were provided
            if raw_notes is not None:
                for order, raw in enumerate(split_note_units(raw_notes), start=1):
                    self.db.add(Note(session_id=session_id, order=order, raw=raw))
            job = ProcessingJob(
                max_attempts=settings.job_max_attempts,
                project_id=project_id,
                session_id=session_id,
                media_id=media_id,
                job_type="initial_index",
                status="pending",
                stage="queued",
                progress=0.0,
            )
            self.db.add(job)
            await self.db.commit()
            loaded = await sessions.get(session_id)
            await self.db.refresh(job)
            assert loaded is not None
            return IngestionResult(loaded, job, True)
        except Exception:
            await self.db.rollback()
            await self.storage.delete(stored.uri)
            raise
