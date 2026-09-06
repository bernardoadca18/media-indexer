import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaAsset, ProcessingJob, Transcription, TranscriptSegment
from app.repositories.domain import JobRepository, SessionRepository
from app.repositories.transcription import TranscriptionRepository
from app.services.storage import Storage
from app.services.transcription_provider import TranscriptionConfig, TranscriptionProvider

logger = structlog.get_logger(__name__)


class SessionNotReadyError(ValueError):
    pass


class TranscriptionAttemptsExhaustedError(ValueError):
    pass


@dataclass(frozen=True)
class TranscriptionRequest:
    model: str
    language: str | None
    beam_size: int
    vad_filter: bool
    word_timestamps: bool
    compute_type: str


@dataclass(frozen=True)
class EnsureTranscriptionResult:
    transcription: Transcription
    job: ProcessingJob
    enqueue: bool
    created: bool


def configuration_fingerprint(provider: str, config: dict[str, Any]) -> str:
    canonical = json.dumps({"provider": provider, **config}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class TranscriptionService:
    def __init__(self, db: AsyncSession, max_attempts: int):
        self.db = db
        self.max_attempts = max_attempts

    async def ensure(
        self, session_id: UUID, request: TranscriptionRequest
    ) -> EnsureTranscriptionResult:
        session = await SessionRepository(self.db).get(session_id)
        if session is None:
            raise LookupError("session not found")
        if session.status != "ready":
            raise SessionNotReadyError("session must be ready before transcription")
        project_id = session.project_id
        stored_session_id = session.id
        media_id = session.media_id

        config: dict[str, Any] = {
            "model": request.model,
            "language": request.language,
            "beam_size": request.beam_size,
            "vad_filter": request.vad_filter,
            "word_timestamps": request.word_timestamps,
            "compute_type": request.compute_type,
        }
        fingerprint = configuration_fingerprint("faster-whisper", config)
        transcriptions = TranscriptionRepository(self.db)
        jobs = JobRepository(self.db)
        existing = await transcriptions.by_fingerprint(media_id, fingerprint)
        if existing is not None:
            job = await jobs.for_transcription(existing.id)
            if job is None:
                raise RuntimeError("transcription exists without processing job")
            enqueue = False
            if job.status == "failed":
                if job.attempt_count >= job.max_attempts:
                    raise TranscriptionAttemptsExhaustedError(
                        "transcription reached its attempt limit"
                    )
                existing.status = "pending"
                existing.started_at = None
                existing.completed_at = None
                existing.language_detected = None
                existing.duration_seconds = None
                existing.provider_metadata = None
                job.status = "pending"
                job.stage = "queued"
                job.progress = 0.0
                job.error = None
                job.completed_at = None
                job.heartbeat_at = None
                enqueue = True
                await self.db.commit()
            return EnsureTranscriptionResult(existing, job, enqueue, False)

        transcription = Transcription(
            project_id=project_id,
            session_id=stored_session_id,
            media_id=media_id,
            provider="faster-whisper",
            model=request.model,
            language_requested=request.language,
            status="pending",
            configuration=config,
            configuration_fingerprint=fingerprint,
        )
        self.db.add(transcription)
        try:
            await self.db.flush()
            job = ProcessingJob(
                project_id=project_id,
                session_id=stored_session_id,
                media_id=media_id,
                transcription_id=transcription.id,
                job_type="transcription",
                status="pending",
                stage="queued",
                progress=0.0,
                max_attempts=self.max_attempts,
            )
            self.db.add(job)
            await self.db.flush()
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            existing = await transcriptions.by_fingerprint(media_id, fingerprint)
            if existing is None:
                raise exc
            existing_job = await jobs.for_transcription(existing.id)
            if existing_job is None:
                raise RuntimeError(
                    "concurrent transcription exists without processing job"
                ) from exc
            return EnsureTranscriptionResult(existing, existing_job, False, False)
        await self.db.refresh(transcription)
        await self.db.refresh(job)
        return EnsureTranscriptionResult(transcription, job, True, True)


class TranscriptionProcessor:
    def __init__(
        self,
        db: AsyncSession,
        storage: Storage,
        provider: TranscriptionProvider,
        batch_size: int,
    ):
        self.db = db
        self.storage = storage
        self.provider = provider
        self.batch_size = batch_size

    async def run(self, job: ProcessingJob) -> None:
        if job.transcription_id is None:
            raise ValueError("transcription job has no transcription_id")
        job_id = job.id
        transcription_id = job.transcription_id
        media_id = job.media_id
        repository = TranscriptionRepository(self.db)
        transcription = await repository.get(transcription_id)
        media = await self.db.get(MediaAsset, media_id)
        if transcription is None or media is None:
            raise LookupError("transcription resources not found")

        await repository.clear_segments(transcription.id)
        transcription.status = "processing"
        transcription.started_at = datetime.now(UTC)
        transcription.completed_at = None
        transcription.language_detected = None
        transcription.duration_seconds = None
        transcription.provider_metadata = None
        job.stage = "transcribing"
        job.progress = 0.01
        await self.db.commit()

        config = TranscriptionConfig(
            model=transcription.model,
            language=transcription.language_requested,
            beam_size=int(transcription.configuration["beam_size"]),
            vad_filter=bool(transcription.configuration["vad_filter"]),
            word_timestamps=bool(transcription.configuration["word_timestamps"]),
        )
        stream = None
        try:
            stream = await self.provider.transcribe(self.storage.resolve_any(media.storage_uri), config)
            transcription.language_detected = stream.language
            transcription.duration_seconds = stream.duration_seconds
            transcription.provider_metadata = stream.metadata
            duration = stream.duration_seconds or media.duration_seconds
            sequence = 0
            buffered = 0
            async for segment in stream.segments:
                sequence += 1
                buffered += 1
                words = None
                if segment.words is not None:
                    words = [
                        {
                            "word": word.word,
                            "start": word.start,
                            "end": word.end,
                            "probability": word.probability,
                        }
                        for word in segment.words
                    ]
                self.db.add(
                    TranscriptSegment(
                        transcription_id=transcription.id,
                        sequence=sequence,
                        start_seconds=segment.start,
                        end_seconds=segment.end,
                        text=segment.text,
                        avg_logprob=segment.avg_logprob,
                        no_speech_prob=segment.no_speech_prob,
                        compression_ratio=segment.compression_ratio,
                        word_timestamps=words,
                    )
                )
                if duration and duration > 0:
                    job.progress = min(0.95, max(0.01, segment.end / duration * 0.95))
                job.heartbeat_at = datetime.now(UTC)
                if buffered >= self.batch_size:
                    await self.db.commit()
                    buffered = 0

            transcription.status = "completed"
            transcription.completed_at = datetime.now(UTC)
            job.stage = "finalizing"
            job.progress = 0.99
            job.heartbeat_at = datetime.now(UTC)
            await self.db.commit()
        except Exception:
            await self.db.rollback()
            transcription = await repository.get(transcription_id)
            if transcription is not None:
                transcription.status = "failed"
                transcription.completed_at = datetime.now(UTC)
                await self.db.commit()
            logger.exception("transcription_failed", job_id=str(job_id))
            raise
        finally:
            if stream is not None and stream.close is not None:
                await stream.close()
