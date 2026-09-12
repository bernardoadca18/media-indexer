import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DerivedAsset, MediaAsset, ProcessingJob, SceneSegment, VisualIndex
from app.repositories.domain import JobRepository, SessionRepository
from app.repositories.visual_index import VisualIndexRepository
from app.services.storage import Storage
from app.services.visual_tools import FrameExtractor, SceneDetector, SceneResult

logger = structlog.get_logger(__name__)


class VisualSessionNotReadyError(ValueError):
    pass


class MediaDurationUnavailableError(ValueError):
    pass


class VisualIndexAttemptsExhaustedError(ValueError):
    pass


@dataclass(frozen=True)
class VisualIndexRequest:
    scene_detection_enabled: bool
    scene_detector: str
    scene_threshold: float
    periodic_frame_interval_seconds: float
    frame_width: int
    image_format: str
    jpeg_quality: int
    dedup_tolerance_seconds: float


@dataclass(frozen=True)
class EnsureVisualIndexResult:
    visual_index: VisualIndex
    job: ProcessingJob
    enqueue: bool
    created: bool


@dataclass
class FrameCandidate:
    timestamp_seconds: float
    origins: set[str] = field(default_factory=set)
    scene_sequences: list[int] = field(default_factory=list)
    source_start_seconds: float | None = None
    source_end_seconds: float | None = None


def visual_configuration_fingerprint(detector: str, config: dict[str, Any]) -> str:
    canonical = json.dumps({"detector": detector, **config}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_scenes(scenes: list[SceneResult], duration_seconds: float) -> list[SceneResult]:
    if not scenes:
        return []
    ordered = sorted(scenes, key=lambda item: (item.start_seconds, item.end_seconds))
    previous_end = 0.0
    validated: list[SceneResult] = []
    for index, scene in enumerate(ordered):
        start = float(scene.start_seconds)
        end = float(scene.end_seconds)
        if start < 0 or end < start or end > duration_seconds + 0.001:
            raise ValueError("scene detector returned an invalid temporal range")
        if index == 0 and abs(start) > 0.001:
            raise ValueError("first scene must start at zero")
        if index > 0 and start < previous_end - 0.001:
            raise ValueError("scene detector returned overlapping scenes")
        if index == 0:
            start = 0.0
        if index == len(ordered) - 1:
            end = duration_seconds
        validated.append(
            SceneResult(
                start_seconds=start,
                end_seconds=end,
                detector_score=scene.detector_score,
                metadata=scene.metadata,
            )
        )
        previous_end = end
    return validated


def representative_candidates(
    duration_seconds: float,
    periodic_interval_seconds: float,
    scenes: list[SceneResult],
    dedup_tolerance_seconds: float,
) -> list[FrameCandidate]:
    if duration_seconds <= 0 or periodic_interval_seconds <= 0:
        raise ValueError("duration and periodic interval must be positive")
    if dedup_tolerance_seconds < 0:
        raise ValueError("dedup tolerance must be nonnegative")

    requested: list[FrameCandidate] = []
    for sequence, scene in enumerate(scenes, start=1):
        midpoint = scene.start_seconds + (scene.end_seconds - scene.start_seconds) / 2
        midpoint = min(max(0.0, midpoint), max(0.0, duration_seconds - 0.001))
        requested.append(
            FrameCandidate(
                timestamp_seconds=midpoint,
                origins={"scene"},
                scene_sequences=[sequence],
                source_start_seconds=scene.start_seconds,
                source_end_seconds=scene.end_seconds,
            )
        )

    periodic_index = 0
    while True:
        timestamp = periodic_index * periodic_interval_seconds
        if timestamp >= duration_seconds:
            break
        requested.append(FrameCandidate(timestamp_seconds=timestamp, origins={"periodic"}))
        periodic_index += 1

    kept: list[FrameCandidate] = []
    for candidate in requested:
        match = next(
            (
                existing
                for existing in kept
                if abs(existing.timestamp_seconds - candidate.timestamp_seconds)
                < dedup_tolerance_seconds
            ),
            None,
        )
        if match is None:
            kept.append(candidate)
            continue
        match.origins.update(candidate.origins)
        match.scene_sequences.extend(candidate.scene_sequences)
        if candidate.source_start_seconds is not None:
            match.source_start_seconds = (
                candidate.source_start_seconds
                if match.source_start_seconds is None
                else min(match.source_start_seconds, candidate.source_start_seconds)
            )
        if candidate.source_end_seconds is not None:
            match.source_end_seconds = (
                candidate.source_end_seconds
                if match.source_end_seconds is None
                else max(match.source_end_seconds, candidate.source_end_seconds)
            )

    return sorted(kept, key=lambda item: item.timestamp_seconds)


class VisualIndexService:
    def __init__(self, db: AsyncSession, max_attempts: int):
        self.db = db
        self.max_attempts = max_attempts

    async def ensure(
        self, session_id: UUID, request: VisualIndexRequest
    ) -> EnsureVisualIndexResult:
        session = await SessionRepository(self.db).get(session_id)
        if session is None:
            raise LookupError("session not found")
        if session.status != "ready":
            raise VisualSessionNotReadyError("session must be ready before visual indexing")
        if session.media.duration_seconds is None or session.media.duration_seconds <= 0:
            raise MediaDurationUnavailableError("media duration is required for visual indexing")
        project_id = session.project_id
        stored_session_id = session.id
        media_id = session.media_id

        config: dict[str, Any] = {
            "scene_detection_enabled": request.scene_detection_enabled,
            "scene_detector": request.scene_detector,
            "scene_threshold": request.scene_threshold,
            "periodic_frame_interval_seconds": request.periodic_frame_interval_seconds,
            "frame_width": request.frame_width,
            "image_format": request.image_format,
            "jpeg_quality": request.jpeg_quality,
            "dedup_tolerance_seconds": request.dedup_tolerance_seconds,
            "scene_representative_strategy": "midpoint",
        }
        fingerprint = visual_configuration_fingerprint(request.scene_detector, config)
        indexes = VisualIndexRepository(self.db)
        jobs = JobRepository(self.db)
        existing = await indexes.by_fingerprint(media_id, fingerprint)
        if existing is not None:
            job = await jobs.for_visual_index(existing.id)
            if job is None:
                raise RuntimeError("visual index exists without processing job")
            enqueue = False
            if job.status == "failed":
                if job.attempt_count >= job.max_attempts:
                    raise VisualIndexAttemptsExhaustedError(
                        "visual index reached its attempt limit"
                    )
                existing.status = "pending"
                existing.started_at = None
                existing.completed_at = None
                existing.tool_metadata = None
                job.status = "pending"
                job.stage = "queued"
                job.progress = 0.0
                job.error = None
                job.completed_at = None
                job.heartbeat_at = None
                enqueue = True
                await self.db.commit()
            return EnsureVisualIndexResult(existing, job, enqueue, False)

        visual_index = VisualIndex(
            project_id=project_id,
            session_id=stored_session_id,
            media_id=media_id,
            status="pending",
            detector=request.scene_detector,
            configuration=config,
            configuration_fingerprint=fingerprint,
        )
        self.db.add(visual_index)
        try:
            await self.db.flush()
            job = ProcessingJob(
                project_id=project_id,
                session_id=stored_session_id,
                media_id=media_id,
                visual_index_id=visual_index.id,
                job_type="visual_index",
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
            existing = await indexes.by_fingerprint(media_id, fingerprint)
            if existing is None:
                raise exc
            existing_job = await jobs.for_visual_index(existing.id)
            if existing_job is None:
                raise RuntimeError("concurrent visual index exists without processing job") from exc
            return EnsureVisualIndexResult(existing, existing_job, False, False)
        await self.db.refresh(visual_index)
        await self.db.refresh(job)
        return EnsureVisualIndexResult(visual_index, job, True, True)


class VisualIndexProcessor:
    def __init__(
        self,
        db: AsyncSession,
        storage: Storage,
        detector: SceneDetector,
        extractor: FrameExtractor,
        batch_size: int,
    ):
        self.db = db
        self.storage = storage
        self.detector = detector
        self.extractor = extractor
        self.batch_size = max(1, batch_size)

    async def run(self, job: ProcessingJob) -> None:
        if job.visual_index_id is None:
            raise ValueError("visual index job has no visual_index_id")
        if job.session_id is None:
            raise ValueError("visual index job has no session_id")
        job_id = job.id
        visual_index_id = job.visual_index_id
        project_id = job.project_id
        session_id = job.session_id
        media_id = job.media_id
        repository = VisualIndexRepository(self.db)
        visual_index = await repository.get(visual_index_id)
        media = await self.db.get(MediaAsset, media_id)
        if visual_index is None or media is None:
            raise LookupError("visual index resources not found")
        if visual_index.detector != self.detector.name:
            raise ValueError(
                f"configured scene detector {visual_index.detector!r} is not available"
            )
        if media.duration_seconds is None or media.duration_seconds <= 0:
            raise MediaDurationUnavailableError("media duration is required for visual indexing")

        duration = media.duration_seconds
        source = self.storage.resolve_any(media.storage_uri)
        await self.storage.delete_derived_namespace(
            project_id, session_id, "visual", visual_index.id
        )
        await repository.clear_outputs(visual_index.id)
        visual_index.status = "processing"
        visual_index.started_at = datetime.now(UTC)
        visual_index.completed_at = None
        visual_index.tool_metadata = None
        job.stage = "detecting_scenes"
        job.progress = 0.05
        await self.db.commit()
        logger.info(
            "visual_index_started",
            project_id=str(project_id),
            session_id=str(session_id),
            media_id=str(media_id),
            visual_index_id=str(visual_index.id),
            job_id=str(job_id),
            duration=duration,
        )

        scene_count = 0
        frame_count = 0
        try:
            configuration = visual_index.configuration
            scenes: list[SceneResult] = []
            if bool(configuration["scene_detection_enabled"]):
                scenes = validate_scenes(
                    await self.detector.detect(
                        source,
                        duration,
                        float(configuration["scene_threshold"]),
                    ),
                    duration,
                )
            for sequence, scene in enumerate(scenes, start=1):
                self.db.add(
                    SceneSegment(
                        visual_index_id=visual_index.id,
                        sequence=sequence,
                        start_seconds=scene.start_seconds,
                        end_seconds=scene.end_seconds,
                        duration_seconds=scene.end_seconds - scene.start_seconds,
                        detector_score=scene.detector_score,
                        segment_metadata=scene.metadata,
                    )
                )
            scene_count = len(scenes)
            job.stage = "extracting_frames"
            job.progress = 0.35
            job.heartbeat_at = datetime.now(UTC)
            await self.db.commit()

            candidates = representative_candidates(
                duration,
                float(configuration["periodic_frame_interval_seconds"]),
                scenes,
                float(configuration["dedup_tolerance_seconds"]),
            )
            for sequence, candidate in enumerate(candidates, start=1):
                extracted = await self.extractor.extract(
                    source,
                    candidate.timestamp_seconds,
                    int(configuration["frame_width"]),
                    str(configuration["image_format"]),
                    int(configuration["jpeg_quality"]),
                )
                filename = (
                    f"frame-{sequence:06d}-" f"{round(candidate.timestamp_seconds * 1000):012d}.jpg"
                )
                stored = await self.storage.save_derived(
                    project_id,
                    session_id,
                    "visual",
                    visual_index.id,
                    "frames",
                    filename,
                    extracted.content,
                )
                self.db.add(
                    DerivedAsset(
                        project_id=project_id,
                        session_id=session_id,
                        media_id=media_id,
                        visual_index_id=visual_index_id,
                        kind="representative_frame",
                        storage_uri=stored.uri,
                        mime_type=extracted.mime_type,
                        byte_size=stored.byte_size,
                        sha256=stored.sha256,
                        width=extracted.width,
                        height=extracted.height,
                        timestamp_seconds=candidate.timestamp_seconds,
                        source_start_seconds=candidate.source_start_seconds,
                        source_end_seconds=candidate.source_end_seconds,
                        asset_metadata={
                            "origins": sorted(candidate.origins),
                            "scene_sequences": sorted(set(candidate.scene_sequences)),
                            "strategy": (
                                "scene_midpoint" if "scene" in candidate.origins else "periodic"
                            ),
                        },
                    )
                )
                frame_count = sequence
                job.progress = 0.35 + 0.6 * sequence / max(1, len(candidates))
                job.heartbeat_at = datetime.now(UTC)
                if sequence % self.batch_size == 0:
                    await self.db.commit()

            visual_index.status = "completed"
            visual_index.completed_at = datetime.now(UTC)
            visual_index.tool_metadata = {
                "scene_detector": self.detector.name,
                "frame_extractor": self.extractor.name,
                "scene_count": scene_count,
                "frame_count": frame_count,
                "duration_seconds": duration,
            }
            job.stage = "finalizing"
            job.progress = 0.99
            job.heartbeat_at = datetime.now(UTC)
            await self.db.commit()
            logger.info(
                "visual_index_completed",
                project_id=str(project_id),
                session_id=str(session_id),
                media_id=str(media_id),
                visual_index_id=str(visual_index_id),
                job_id=str(job_id),
                stage="finalizing",
                scene_count=scene_count,
                frame_count=frame_count,
                duration=duration,
            )
        except Exception:
            await self.db.rollback()
            try:
                await self.storage.delete_derived_namespace(
                    project_id, session_id, "visual", visual_index_id
                )
            except Exception:
                logger.exception(
                    "visual_index_storage_cleanup_failed",
                    visual_index_id=str(visual_index_id),
                    job_id=str(job_id),
                )
            visual_index = await repository.get(visual_index_id)
            if visual_index is not None:
                await repository.clear_outputs(visual_index.id)
                visual_index.status = "failed"
                visual_index.completed_at = datetime.now(UTC)
                await self.db.commit()
            logger.exception(
                "visual_index_failed",
                project_id=str(project_id),
                session_id=str(session_id),
                media_id=str(media_id),
                visual_index_id=str(visual_index_id),
                job_id=str(job_id),
                stage="failed",
                scene_count=scene_count,
                frame_count=frame_count,
                duration=duration,
            )
            raise
