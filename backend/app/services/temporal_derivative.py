import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaAsset, ProcessingJob, TemporalDerivative
from app.repositories.domain import (
    JobRepository,
    MediaAssetRepository,
    TemporalDerivativeRepository,
)
from app.schemas.temporal_derivative import (
    TemporalDerivativeEnsureRequest,
    TemporalDerivativeProfile,
)
from app.services.probe import FFprobe, ProbeResult
from app.services.storage import LargeDerivedTarget, Storage, StoredObject


class TemporalDerivativeConflictError(ValueError):
    pass


class TemporalDerivativeAttemptsExhaustedError(ValueError):
    pass


@dataclass(frozen=True)
class EnsureTemporalDerivativeResult:
    derivative: TemporalDerivative
    job: ProcessingJob
    enqueue: bool
    created: bool


def canonical_fingerprint(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def profile_fingerprint(profile: TemporalDerivativeProfile) -> str:
    return canonical_fingerprint(profile.model_dump(mode="json"))


def derivative_identity(
    source_sha256: str,
    source_start_microseconds: int,
    source_end_microseconds: int,
    profile_identity: str,
    strategy_version: str,
) -> str:
    return canonical_fingerprint(
        {
            "profile_identity": profile_identity,
            "source_end_microseconds": source_end_microseconds,
            "source_sha256": source_sha256,
            "source_start_microseconds": source_start_microseconds,
            "strategy_version": strategy_version,
        }
    )


class TemporalDerivativeService:
    def __init__(self, db: AsyncSession, storage: Storage, max_attempts: int):
        self.db = db
        self.storage = storage
        self.max_attempts = max_attempts

    async def ensure(
        self, media_id: UUID, request: TemporalDerivativeEnsureRequest
    ) -> EnsureTemporalDerivativeResult:
        media = await MediaAssetRepository(self.db).get(media_id)
        if media is None:
            raise LookupError("media asset not found")
        self._validate_request(media, request)
        identity = derivative_identity(
            request.source_sha256,
            request.source_start_microseconds,
            request.source_end_microseconds,
            request.profile_identity,
            request.profile.strategy_version,
        )
        derivatives = TemporalDerivativeRepository(self.db)
        jobs = JobRepository(self.db)
        existing = await derivatives.by_media_and_identity(media_id, identity)
        if existing is not None:
            job = await jobs.for_temporal_derivative(existing.id)
            if job is None:
                raise RuntimeError("temporal derivative exists without processing job")
            enqueue = False
            if existing.status == "completed" and not await self._completed_file_exists(existing):
                existing.status = "failed"
                existing.error = "completed derivative content is unavailable"
                job.status = "failed"
                job.stage = "failed"
                job.error = existing.error
            if job.status == "failed":
                if job.attempt_count >= job.max_attempts:
                    raise TemporalDerivativeAttemptsExhaustedError(
                        "temporal derivative reached its attempt limit"
                    )
                existing.status = "pending"
                existing.error = None
                existing.completed_at = None
                job.status = "pending"
                job.stage = "queued"
                job.progress = 0.0
                job.error = None
                job.completed_at = None
                job.heartbeat_at = None
                enqueue = True
                await self.db.commit()
            return EnsureTemporalDerivativeResult(existing, job, enqueue, False)

        project_id = media.project_id
        derivative = TemporalDerivative(
            project_id=project_id,
            source_media_id=media.id,
            identity=identity,
            source_sha256=request.source_sha256,
            source_start_microseconds=request.source_start_microseconds,
            source_end_microseconds=request.source_end_microseconds,
            profile_id=request.profile.profile_id,
            profile_identity=request.profile_identity,
            strategy_version=request.profile.strategy_version,
            specification=request.profile.model_dump(mode="json"),
            status="pending",
        )
        self.db.add(derivative)
        try:
            await self.db.flush()
            job = ProcessingJob(
                project_id=project_id,
                session_id=None,
                media_id=media.id,
                temporal_derivative_id=derivative.id,
                job_type="temporal_derivative",
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
            concurrent = await derivatives.by_media_and_identity(media_id, identity)
            if concurrent is None:
                raise exc
            concurrent_job = await jobs.for_temporal_derivative(concurrent.id)
            if concurrent_job is None:
                raise RuntimeError(
                    "concurrent temporal derivative exists without processing job"
                ) from exc
            return EnsureTemporalDerivativeResult(concurrent, concurrent_job, False, False)
        await self.db.refresh(derivative)
        await self.db.refresh(job)
        return EnsureTemporalDerivativeResult(derivative, job, True, True)

    def _validate_request(
        self, media: MediaAsset, request: TemporalDerivativeEnsureRequest
    ) -> None:
        if media.media_kind != "video":
            raise TemporalDerivativeConflictError("temporal derivatives require a video source")
        if request.source_sha256 != media.sha256:
            raise TemporalDerivativeConflictError("source sha256 does not match canonical media")
        if request.source_start_microseconds >= request.source_end_microseconds:
            raise TemporalDerivativeConflictError("source range must be increasing")
        if media.duration_seconds is None or media.duration_seconds <= 0:
            raise TemporalDerivativeConflictError("source duration is unavailable")
        duration_microseconds = round(media.duration_seconds * 1_000_000)
        if request.source_end_microseconds > duration_microseconds:
            raise TemporalDerivativeConflictError("source range exceeds canonical media duration")
        if request.profile_identity != profile_fingerprint(request.profile):
            raise TemporalDerivativeConflictError("profile identity is invalid")

    async def _completed_file_exists(self, derivative: TemporalDerivative) -> bool:
        if (
            derivative.storage_uri is None
            or derivative.byte_size is None
            or derivative.sha256 is None
        ):
            return False
        try:
            path = self.storage.resolve_any(derivative.storage_uri)
            is_file = await asyncio.to_thread(path.is_file)
            if not is_file:
                return False
            details = await asyncio.to_thread(path.stat)
            if details.st_size != derivative.byte_size:
                return False
            actual_sha256 = await asyncio.to_thread(_hash_file, path)
            return actual_sha256 == derivative.sha256
        except (OSError, ValueError):
            return False


class TemporalDerivativeProcessor:
    def __init__(
        self,
        db: AsyncSession,
        storage: Storage,
        ffmpeg_binary: str,
        probe: FFprobe,
    ):
        self.db = db
        self.storage = storage
        self.ffmpeg_binary = ffmpeg_binary
        self.probe = probe

    async def run(self, job: ProcessingJob) -> None:
        if job.temporal_derivative_id is None:
            raise ValueError("temporal derivative job has no derivative id")
        derivative = await TemporalDerivativeRepository(self.db).get(job.temporal_derivative_id)
        media = await self.db.get(MediaAsset, job.media_id)
        if derivative is None or media is None:
            raise LookupError("temporal derivative resources not found")
        if media.sha256 != derivative.source_sha256:
            raise TemporalDerivativeConflictError("canonical source identity changed")
        profile = TemporalDerivativeProfile.model_validate(derivative.specification)
        if profile_fingerprint(profile) != derivative.profile_identity:
            raise TemporalDerivativeConflictError("persisted derivative profile is invalid")
        source = self.storage.resolve_any(media.storage_uri)
        if not source.is_file():
            raise FileNotFoundError("canonical source content is unavailable")
        target = await self.storage.create_large_derived_target(
            media.storage_uri,
            derivative.project_id,
            media.id,
            derivative.identity,
            "proxy.mp4",
        )
        derivative.status = "processing"
        derivative.started_at = derivative.started_at or datetime.now(UTC)
        derivative.error = None
        job.stage = "materializing_temporal_derivative"
        job.progress = 0.1
        await self.db.commit()
        started = monotonic()
        try:
            recovered = await self._recover_promoted(target, derivative, profile)
            if recovered is None:
                await self._generate(source, target, derivative, profile)
                probe = await self._validate(target.temporary_path, derivative, profile)
                stored = await self.storage.promote_large_derived(target)
            else:
                probe, stored = recovered
            derivative.storage_uri = stored.uri
            derivative.sha256 = stored.sha256
            derivative.byte_size = stored.byte_size
            derivative.duration_seconds = probe.duration_seconds
            derivative.width = probe.width
            derivative.height = probe.height
            derivative.fps = probe.fps
            derivative.codec = probe.video_codec
            derivative.generation_seconds = monotonic() - started
            derivative.status = "completed"
            derivative.completed_at = datetime.now(UTC)
            derivative.error = None
            job.stage = "persisting_temporal_derivative"
            job.progress = 0.95
            await self.db.commit()
        except Exception:
            await self.storage.discard_large_derived(target)
            raise

    async def _recover_promoted(
        self,
        target: LargeDerivedTarget,
        derivative: TemporalDerivative,
        profile: TemporalDerivativeProfile,
    ) -> tuple[ProbeResult, StoredObject] | None:
        if not target.final_path.is_file():
            return None
        try:
            probe = await self._validate(target.final_path, derivative, profile)
            byte_size, sha256 = await asyncio.to_thread(_hash_path, target.final_path)
            return probe, StoredObject(target.uri, byte_size, sha256)
        except Exception:
            await asyncio.to_thread(target.final_path.unlink, missing_ok=True)
            return None

    async def _generate(
        self,
        source: Path,
        target: LargeDerivedTarget,
        derivative: TemporalDerivative,
        profile: TemporalDerivativeProfile,
    ) -> None:
        start = derivative.source_start_microseconds / 1_000_000
        duration = (
            derivative.source_end_microseconds - derivative.source_start_microseconds
        ) / 1_000_000
        scale = (
            f"scale=w='min(iw,{profile.max_width})':h='min(ih,{profile.max_height})'"
            ":force_original_aspect_ratio=decrease:force_divisible_by=2"
        )
        process = await asyncio.create_subprocess_exec(
            self.ffmpeg_binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-ss",
            f"{start:.6f}",
            "-i",
            str(source),
            "-t",
            f"{duration:.6f}",
            "-map",
            "0:v:0",
            "-vf",
            f"setpts=PTS-STARTPTS,fps={profile.fps},{scale}",
            "-an",
            "-sn",
            "-dn",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            profile.encoder_backend,
            "-preset",
            profile.preset,
            "-crf",
            str(profile.crf),
            "-pix_fmt",
            profile.pixel_format,
            "-g",
            str(profile.gop_frames),
            "-keyint_min",
            str(profile.gop_frames),
            "-sc_threshold",
            "0",
            "-movflags",
            "+faststart",
            "-f",
            profile.container,
            "-y",
            str(target.temporary_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace").strip() or "ffmpeg failed")

    async def _validate(
        self,
        path: Path,
        derivative: TemporalDerivative,
        profile: TemporalDerivativeProfile,
    ) -> ProbeResult:
        probe = await self.probe.inspect(path)
        expected_duration = (
            derivative.source_end_microseconds - derivative.source_start_microseconds
        ) / 1_000_000
        streams = probe.raw.get("streams", [])
        video_streams = [item for item in streams if item.get("codec_type") == "video"]
        if (
            len(video_streams) != 1
            or len(streams) != 1
            or probe.video_codec != "h264"
            or probe.audio_codec is not None
            or probe.duration_seconds is None
            or abs(probe.duration_seconds - expected_duration) > 1 / profile.fps + 0.005
            or probe.width is None
            or probe.height is None
            or probe.width > profile.max_width
            or probe.height > profile.max_height
            or probe.fps is None
            or abs(probe.fps - profile.fps) > 0.01
        ):
            raise RuntimeError("generated temporal derivative failed validation")
        return probe


def _hash_path(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
