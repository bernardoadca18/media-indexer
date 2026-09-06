import asyncio
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AlignmentRun,
    AlignmentSubject,
    CandidateAlignment,
    DerivedAsset,
    MediaAsset,
    Note,
    ProcessingJob,
    RetrievalCandidate,
)
from app.repositories.alignment import AlignmentRepository
from app.repositories.candidate_retrieval import CandidateEvidenceRecord
from app.repositories.domain import JobRepository
from app.services.alignment_prompt import ALIGNMENT_PROMPT_VERSION, ALIGNMENT_STRATEGY
from app.services.alignment_provider import (
    AlignmentFrameEvidence,
    AlignmentProviderRequest,
    AlignmentProviderResponse,
    AlignmentTranscriptEvidence,
    MultimodalAlignmentProvider,
    validate_result_bounds,
)
from app.services.candidate_retrieval import CandidateEvidenceService
from app.services.storage import Storage
from app.services.visual_tools import FrameExtractor

logger = structlog.get_logger(__name__)


class AlignmentPrerequisiteError(ValueError):
    pass


class AlignmentAttemptsExhaustedError(ValueError):
    pass


@dataclass(frozen=True)
class AlignmentConfig:
    frame_interval_seconds: float = 2.0
    max_frames: int = 60
    frame_width: int = 640
    jpeg_quality: int = 82
    frame_dedup_tolerance_seconds: float = 0.25
    max_transcript_characters: int = 12000
    refinement_enabled: bool = False
    refinement_frame_interval_seconds: float = 0.5
    refinement_padding_seconds: float = 4.0
    refinement_max_frames: int = 40
    early_stop_confidence: float = 0.90

    def validate(self) -> None:
        if self.frame_interval_seconds <= 0 or self.max_frames < 1:
            raise ValueError("invalid coarse frame sampling configuration")
        if self.frame_width <= 0 or not 1 <= self.jpeg_quality <= 100:
            raise ValueError("invalid alignment frame encoding configuration")
        if self.frame_dedup_tolerance_seconds < 0:
            raise ValueError("frame dedup tolerance must be nonnegative")
        if self.max_transcript_characters < 1:
            raise ValueError("transcript limit must be positive")
        if self.refinement_frame_interval_seconds <= 0:
            raise ValueError("refinement frame interval must be positive")
        if self.refinement_padding_seconds < 0 or self.refinement_max_frames < 1:
            raise ValueError("invalid refinement configuration")
        if not 0 <= self.early_stop_confidence <= 1:
            raise ValueError("early stop confidence must be between 0 and 1")


@dataclass(frozen=True)
class AlignmentRequest:
    model: str
    candidate_ids: tuple[UUID, ...] | None
    config: AlignmentConfig


@dataclass(frozen=True)
class EnsureAlignmentResult:
    alignment_run: AlignmentRun
    job: ProcessingJob
    enqueue: bool
    created: bool


@dataclass(frozen=True)
class AlignmentSubjectSpec:
    subject_key: str
    note_id: UUID | None
    observation: str


@dataclass(frozen=True)
class FrameSamplingPlan:
    timestamps: tuple[float, ...]
    resolution_seconds: float


def alignment_fingerprint(configuration: dict[str, Any]) -> str:
    canonical = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def sample_frame_timestamps(
    start_seconds: float,
    end_seconds: float,
    interval_seconds: float,
    max_frames: int,
) -> FrameSamplingPlan:
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise ValueError("invalid alignment frame window")
    if interval_seconds <= 0 or max_frames < 1:
        raise ValueError("invalid alignment frame sampling configuration")
    duration = end_seconds - start_seconds
    desired = math.floor(duration / interval_seconds) + 1
    if desired <= max_frames:
        values = tuple(start_seconds + index * interval_seconds for index in range(desired))
        resolution = interval_seconds
    elif max_frames == 1:
        values = (start_seconds + duration / 2.0,)
        resolution = duration
    else:
        resolution = duration / (max_frames - 1)
        values = tuple(start_seconds + index * resolution for index in range(max_frames))
    return FrameSamplingPlan(values, resolution)


def refinement_window(
    coarse_start: float,
    coarse_end: float,
    candidate_start: float,
    candidate_end: float,
    padding_seconds: float,
) -> tuple[float, float]:
    start = max(candidate_start, coarse_start - padding_seconds)
    end = min(candidate_end, coarse_end + padding_seconds)
    if end <= start:
        raise ValueError("invalid refinement window")
    return start, end


def _transcript_evidence(
    evidence: CandidateEvidenceRecord,
    start_seconds: float,
    end_seconds: float,
    max_characters: int,
) -> tuple[tuple[AlignmentTranscriptEvidence, ...], bool]:
    selected: list[AlignmentTranscriptEvidence] = []
    used = 0
    truncated = False
    for segment in evidence.transcript_segments:
        if segment.end_seconds <= start_seconds or segment.start_seconds >= end_seconds:
            continue
        remaining = max_characters - used
        if remaining <= 0:
            truncated = True
            break
        text = segment.text
        if len(text) > remaining:
            text = text[:remaining]
            truncated = True
        selected.append(
            AlignmentTranscriptEvidence(
                segment.start_seconds,
                segment.end_seconds,
                text,
            )
        )
        used += len(text)
        if truncated:
            break
    return tuple(selected), truncated


def _subject_definitions(configuration: dict[str, Any]) -> list[dict[str, Any]]:
    subjects = configuration.get("subjects")
    if not isinstance(subjects, list):
        raise AlignmentPrerequisiteError("invalid retrieval subject provenance")
    result: list[dict[str, Any]] = []
    for item in subjects:
        if not isinstance(item, dict) or not isinstance(item.get("subject_key"), str):
            raise AlignmentPrerequisiteError("invalid retrieval subject provenance")
        result.append(item)
    return result


class AlignmentService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        provider_name: str,
        max_attempts: int,
    ):
        self.db = db
        self.provider_name = provider_name
        self.max_attempts = max_attempts
        self.repository = AlignmentRepository(db)

    async def ensure(
        self,
        retrieval_run_id: UUID,
        request: AlignmentRequest,
    ) -> EnsureAlignmentResult:
        request.config.validate()
        if not request.model.strip():
            raise ValueError("alignment model must not be blank")
        retrieval_run = await self.repository.retrieval_run(retrieval_run_id)
        if retrieval_run is None:
            raise LookupError("retrieval run not found")
        if retrieval_run.status != "completed":
            raise AlignmentPrerequisiteError("retrieval run must be completed before alignment")
        project_id = retrieval_run.project_id
        session_id = retrieval_run.session_id
        media_id = retrieval_run.media_id
        stored_retrieval_run_id = retrieval_run.id
        candidate_ids = set(request.candidate_ids) if request.candidate_ids is not None else None
        if candidate_ids is not None and len(candidate_ids) != len(request.candidate_ids or ()):
            raise AlignmentPrerequisiteError("candidate_ids must not contain duplicates")
        candidates = list(await self.repository.candidates(stored_retrieval_run_id, candidate_ids))
        if candidate_ids is not None and {item.id for item in candidates} != candidate_ids:
            raise AlignmentPrerequisiteError("every candidate must belong to the retrieval run")
        configured_subjects = _subject_definitions(retrieval_run.configuration)
        if candidate_ids is None:
            subject_keys = [item["subject_key"] for item in configured_subjects]
        else:
            selected_keys = {item.subject_key for item in candidates}
            subject_keys = [
                item["subject_key"]
                for item in configured_subjects
                if item["subject_key"] in selected_keys
            ]
        configuration: dict[str, Any] = {
            "retrieval_run_id": str(stored_retrieval_run_id),
            "candidate_ids": [str(item.id) for item in candidates],
            "subject_keys": subject_keys,
            "provider": self.provider_name,
            "model": request.model,
            "strategy": ALIGNMENT_STRATEGY,
            "prompt_version": ALIGNMENT_PROMPT_VERSION,
            "frame_sampling": asdict(request.config),
            "provider_retry_policy": "job_only",
            "result_schema": "alignment_result_v1",
        }
        fingerprint = alignment_fingerprint(configuration)
        existing = await self.repository.by_fingerprint(
            stored_retrieval_run_id,
            fingerprint,
        )
        if existing is not None:
            job = await JobRepository(self.db).for_alignment_run(existing.id)
            if job is None:
                raise RuntimeError("alignment run exists without processing job")
            enqueue = False
            if job.status == "failed":
                if job.attempt_count >= job.max_attempts:
                    raise AlignmentAttemptsExhaustedError("alignment run reached its attempt limit")
                existing.status = "pending"
                existing.started_at = None
                existing.completed_at = None
                existing.provider_metadata = None
                existing.usage_metadata = None
                job.status = "pending"
                job.stage = "queued"
                job.progress = 0.0
                job.error = None
                job.completed_at = None
                job.heartbeat_at = None
                enqueue = True
                await self.db.commit()
            return EnsureAlignmentResult(existing, job, enqueue, False)
        run = AlignmentRun(
            project_id=project_id,
            session_id=session_id,
            media_id=media_id,
            retrieval_run_id=stored_retrieval_run_id,
            provider=self.provider_name,
            model=request.model,
            strategy=ALIGNMENT_STRATEGY,
            prompt_version=ALIGNMENT_PROMPT_VERSION,
            status="pending",
            configuration=configuration,
            configuration_fingerprint=fingerprint,
        )
        self.db.add(run)
        try:
            await self.db.flush()
            job = ProcessingJob(
                project_id=project_id,
                session_id=session_id,
                media_id=media_id,
                alignment_run_id=run.id,
                job_type="multimodal_alignment",
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
            concurrent = await self.repository.by_fingerprint(
                stored_retrieval_run_id,
                fingerprint,
            )
            if concurrent is None:
                raise exc
            concurrent_job = await JobRepository(self.db).for_alignment_run(concurrent.id)
            if concurrent_job is None:
                raise RuntimeError(
                    "concurrent alignment run exists without processing job"
                ) from exc
            return EnsureAlignmentResult(concurrent, concurrent_job, False, False)
        await self.db.refresh(run)
        await self.db.refresh(job)
        return EnsureAlignmentResult(run, job, True, True)


class AlignmentEvidenceBuilder:
    def __init__(
        self,
        db: AsyncSession,
        storage: Storage,
        extractor: FrameExtractor,
    ):
        self.db = db
        self.storage = storage
        self.extractor = extractor

    async def build(
        self,
        *,
        run: AlignmentRun,
        candidate: RetrievalCandidate,
        observation: str,
        note_id: UUID | None,
        source: Path,
        pass_name: str,
        window_start: float,
        window_end: float,
        interval_seconds: float,
        max_frames: int,
        config: AlignmentConfig,
    ) -> AlignmentProviderRequest:
        evidence = await CandidateEvidenceService(self.db).get(candidate.id)
        plan = sample_frame_timestamps(
            window_start,
            window_end,
            interval_seconds,
            max_frames,
        )
        frames: list[AlignmentFrameEvidence] = []
        for sequence, timestamp in enumerate(plan.timestamps, start=1):
            extracted = await self.extractor.extract(
                source,
                timestamp,
                config.frame_width,
                "jpeg",
                config.jpeg_quality,
            )
            filename = (
                f"{candidate.id}-{pass_name}-{sequence:04d}-" f"{round(timestamp * 1000):012d}.jpg"
            )
            stored = await self.storage.save_derived(
                run.project_id,
                run.session_id,
                "alignment",
                run.id,
                "frames",
                filename,
                extracted.content,
            )
            self.db.add(
                DerivedAsset(
                    project_id=run.project_id,
                    session_id=run.session_id,
                    media_id=run.media_id,
                    alignment_run_id=run.id,
                    kind="alignment_frame",
                    storage_uri=stored.uri,
                    mime_type=extracted.mime_type,
                    byte_size=stored.byte_size,
                    sha256=stored.sha256,
                    width=extracted.width,
                    height=extracted.height,
                    timestamp_seconds=timestamp,
                    source_start_seconds=window_start,
                    source_end_seconds=window_end,
                    asset_metadata={
                        "candidate_id": str(candidate.id),
                        "pass": pass_name,
                        "sampling_resolution_seconds": plan.resolution_seconds,
                    },
                )
            )
            frames.append(
                AlignmentFrameEvidence(
                    timestamp,
                    extracted.content,
                    extracted.mime_type,
                    stored.uri,
                    "alignment",
                )
            )
        await self.db.commit()
        for representative in evidence.frames:
            representative_timestamp = representative.timestamp_seconds
            if len(frames) >= max_frames:
                break
            if (
                representative_timestamp is None
                or not window_start <= representative_timestamp <= window_end
            ):
                continue
            if any(
                abs(item.timestamp_seconds - representative_timestamp)
                < config.frame_dedup_tolerance_seconds
                for item in frames
            ):
                continue
            content = await asyncio.to_thread(
                self.storage.resolve(representative.storage_uri).read_bytes
            )
            frames.append(
                AlignmentFrameEvidence(
                    representative_timestamp,
                    content,
                    representative.mime_type,
                    representative.storage_uri,
                    "representative",
                )
            )
        frames.sort(key=lambda item: (item.timestamp_seconds, item.origin))
        transcript, truncated = _transcript_evidence(
            evidence,
            window_start,
            window_end,
            config.max_transcript_characters,
        )
        return AlignmentProviderRequest(
            candidate_id=candidate.id,
            note_id=note_id,
            observation=observation,
            candidate_start_seconds=candidate.start_seconds,
            candidate_end_seconds=candidate.end_seconds,
            retrieval_rank=candidate.rank,
            retrieval_score=candidate.score,
            pass_name="coarse" if pass_name == "coarse" else "refinement",
            timestamp_resolution_seconds=plan.resolution_seconds,
            transcript=transcript,
            transcript_truncated=truncated,
            frames=tuple(frames),
        )


class AlignmentProcessor:
    def __init__(
        self,
        db: AsyncSession,
        storage: Storage,
        extractor: FrameExtractor,
        provider: MultimodalAlignmentProvider,
    ):
        self.db = db
        self.storage = storage
        self.extractor = extractor
        self.provider = provider
        self.repository = AlignmentRepository(db)

    async def run(self, job: ProcessingJob) -> None:
        if job.alignment_run_id is None:
            raise ValueError("alignment job has no alignment_run_id")
        job_id = job.id
        run_id = job.alignment_run_id
        current_stage = "preparing_evidence"
        run = await self.repository.get_run(run_id)
        if run is None:
            raise LookupError("alignment run not found")
        if run.provider != self.provider.name:
            raise AlignmentPrerequisiteError("configured alignment provider unavailable")
        retrieval = await self.repository.retrieval_run(run.retrieval_run_id)
        media = await self.db.get(MediaAsset, run.media_id)
        if retrieval is None or media is None:
            raise LookupError("alignment prerequisites not found")
        if retrieval.status != "completed":
            raise AlignmentPrerequisiteError("retrieval run must remain completed")
        if media.duration_seconds is None or media.duration_seconds <= 0:
            raise AlignmentPrerequisiteError("media duration is required")
        await self.storage.delete_derived_namespace(
            run.project_id,
            run.session_id,
            "alignment",
            run.id,
        )
        await self.repository.clear_outputs(run.id)
        run.status = "processing"
        run.started_at = datetime.now(UTC)
        run.completed_at = None
        run.provider_metadata = None
        run.usage_metadata = None
        job.stage = current_stage
        job.progress = 0.02
        await self.db.commit()
        config = AlignmentConfig(**run.configuration["frame_sampling"])
        candidate_ids = {UUID(item) for item in run.configuration["candidate_ids"]}
        candidates = list(await self.repository.candidates(retrieval.id, candidate_ids))
        by_subject: dict[str, list[RetrievalCandidate]] = {}
        for candidate in candidates:
            by_subject.setdefault(candidate.subject_key, []).append(candidate)
        specs = await self._subject_specs(
            retrieval.configuration,
            run.configuration,
        )
        source = self.storage.resolve_any(media.storage_uri)
        builder = AlignmentEvidenceBuilder(self.db, self.storage, self.extractor)
        usage: list[dict[str, Any]] = []
        provider_meta: list[dict[str, Any]] = []
        calls = 0
        processed = 0
        try:
            for spec in specs:
                subject = AlignmentSubject(
                    alignment_run_id=run.id,
                    subject_key=spec.subject_key,
                    note_id=spec.note_id,
                    observation=spec.observation,
                    status="pending",
                )
                self.db.add(subject)
                await self.db.flush()
                pairs: list[tuple[CandidateAlignment, RetrievalCandidate]] = []
                for candidate in sorted(
                    by_subject.get(spec.subject_key, []),
                    key=lambda item: item.rank,
                ):
                    result = CandidateAlignment(
                        alignment_run_id=run.id,
                        alignment_subject_id=subject.id,
                        retrieval_candidate_id=candidate.id,
                        note_id=spec.note_id,
                        retrieval_rank=candidate.rank,
                        status="pending",
                        anchors=[],
                        pass_count=0,
                    )
                    self.db.add(result)
                    pairs.append((result, candidate))
                await self.db.commit()
                if not pairs:
                    subject.status = "no_candidates"
                    await self.db.commit()
                    continue
                stop = False
                for result, candidate in pairs:
                    if stop:
                        result.status = "not_evaluated"
                        result.completed_at = datetime.now(UTC)
                        continue
                    result.status = "processing"
                    current_stage = "coarse_alignment"
                    job.stage = current_stage
                    result.started_at = datetime.now(UTC)
                    job.progress = 0.05 + 0.90 * processed / max(1, len(candidates))
                    job.heartbeat_at = datetime.now(UTC)
                    await self.db.commit()
                    request = await builder.build(
                        run=run,
                        candidate=candidate,
                        observation=spec.observation,
                        note_id=spec.note_id,
                        source=source,
                        pass_name="coarse",
                        window_start=candidate.start_seconds,
                        window_end=candidate.end_seconds,
                        interval_seconds=config.frame_interval_seconds,
                        max_frames=config.max_frames,
                        config=config,
                    )
                    response = await self._call(run, candidate, request, "coarse")
                    calls += 1
                    self._metadata(response, usage, provider_meta)
                    final_request = request
                    final_response = response
                    if response.result.matched and config.refinement_enabled:
                        assert response.result.start_seconds is not None
                        assert response.result.end_seconds is not None
                        refine_start, refine_end = refinement_window(
                            response.result.start_seconds,
                            response.result.end_seconds,
                            candidate.start_seconds,
                            candidate.end_seconds,
                            config.refinement_padding_seconds,
                        )
                        current_stage = "refining_alignment"
                        job.stage = current_stage
                        job.heartbeat_at = datetime.now(UTC)
                        await self.db.commit()
                        final_request = await builder.build(
                            run=run,
                            candidate=candidate,
                            observation=spec.observation,
                            note_id=spec.note_id,
                            source=source,
                            pass_name="refinement",
                            window_start=refine_start,
                            window_end=refine_end,
                            interval_seconds=config.refinement_frame_interval_seconds,
                            max_frames=config.refinement_max_frames,
                            config=config,
                        )
                        final_response = await self._call(
                            run,
                            candidate,
                            final_request,
                            "refinement",
                        )
                        calls += 1
                        self._metadata(final_response, usage, provider_meta)
                    self._apply(
                        result,
                        final_response,
                        final_request.timestamp_resolution_seconds,
                        2 if final_request.pass_name == "refinement" else 1,
                    )
                    processed += 1
                    stop = (
                        result.status == "matched"
                        and result.confidence is not None
                        and result.confidence >= config.early_stop_confidence
                    )
                await self.db.commit()
                matched = [
                    (result, candidate) for result, candidate in pairs if result.status == "matched"
                ]
                if matched:
                    selected = min(
                        matched,
                        key=lambda item: (
                            -(item[0].confidence or 0.0),
                            item[1].rank,
                            -item[1].score,
                        ),
                    )[0]
                    subject.selected_candidate_alignment_id = selected.id
                    subject.status = "matched"
                elif all(result.status in {"unmatched", "not_evaluated"} for result, _ in pairs):
                    subject.status = "unmatched"
                else:
                    subject.status = "failed"
                await self.db.commit()
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            run.usage_metadata = {"provider_calls": calls, "responses": usage}
            run.provider_metadata = {"responses": provider_meta}
            current_stage = "finalizing"
            job.stage = current_stage
            job.progress = 0.99
            job.heartbeat_at = datetime.now(UTC)
            await self.db.commit()
            logger.info(
                "multimodal_alignment_completed",
                project_id=str(run.project_id),
                session_id=str(run.session_id),
                media_id=str(run.media_id),
                retrieval_run_id=str(run.retrieval_run_id),
                alignment_run_id=str(run.id),
                job_id=str(job.id),
                model=run.model,
                candidate_count=len(candidates),
                provider_calls=calls,
            )
        except Exception as exc:
            await self.db.rollback()
            failed = await self.repository.get_run(run_id)
            if failed is not None:
                results, _ = await self.repository.results(run_id, 0, 10000)
                for result in results:
                    if result.status == "processing":
                        result.status = "failed"
                        result.error = str(exc)[:4000]
                        result.start_seconds = None
                        result.completed_at = datetime.now(UTC)
                        result.end_seconds = None
                        result.confidence = None
                for subject in await self.repository.subjects(run_id):
                    if subject.status == "pending":
                        subject.status = "failed"
                failed.status = "failed"
                failed.completed_at = datetime.now(UTC)
                failed.provider_metadata = {"error_type": type(exc).__name__}
                await self.db.commit()
            logger.exception(
                "multimodal_alignment_failed",
                alignment_run_id=str(run_id),
                job_id=str(job_id),
                stage=current_stage,
            )
            raise

    async def _subject_specs(
        self,
        retrieval_configuration: dict[str, Any],
        alignment_configuration: dict[str, Any],
    ) -> list[AlignmentSubjectSpec]:
        allowed = set(alignment_configuration["subject_keys"])
        specs: list[AlignmentSubjectSpec] = []
        for item in _subject_definitions(retrieval_configuration):
            key = item["subject_key"]
            if key not in allowed:
                continue
            raw_note_id = item.get("note_id")
            note_id = UUID(raw_note_id) if raw_note_id else None
            if note_id is not None:
                note = await self.db.get(Note, note_id)
                if note is None:
                    raise AlignmentPrerequisiteError("alignment note not found")
                observation = note.raw
            else:
                query = item.get("query")
                if not isinstance(query, str) or not query:
                    raise AlignmentPrerequisiteError("query observation missing")
                observation = query
            specs.append(AlignmentSubjectSpec(key, note_id, observation))
        return specs

    async def _call(
        self,
        run: AlignmentRun,
        candidate: RetrievalCandidate,
        request: AlignmentProviderRequest,
        pass_name: str,
    ) -> AlignmentProviderResponse:
        response = await self.provider.align(request, model=run.model)
        validate_result_bounds(response.result, request)
        logger.info(
            "candidate_alignment_provider_result",
            alignment_run_id=str(run.id),
            retrieval_run_id=str(run.retrieval_run_id),
            candidate_id=str(candidate.id),
            note_id=str(candidate.note_id) if candidate.note_id else None,
            model=run.model,
            pass_name=pass_name,
            frame_count=len(request.frames),
            candidate_duration=candidate.end_seconds - candidate.start_seconds,
            matched=response.result.matched,
            confidence=response.result.confidence,
            latency=response.latency_seconds,
        )
        return response

    @staticmethod
    def _metadata(
        response: AlignmentProviderResponse,
        usage: list[dict[str, Any]],
        provider: list[dict[str, Any]],
    ) -> None:
        if response.usage_metadata:
            usage.append(response.usage_metadata)
        if response.provider_metadata:
            provider.append(response.provider_metadata)

    @staticmethod
    def _apply(
        target: CandidateAlignment,
        response: AlignmentProviderResponse,
        resolution_seconds: float,
        pass_count: int,
    ) -> None:
        result = response.result
        target.status = "matched" if result.matched else "unmatched"
        target.start_seconds = result.start_seconds
        target.end_seconds = result.end_seconds
        target.confidence = result.confidence
        target.anchors = [anchor.model_dump(mode="json") for anchor in result.anchors]
        target.reasoning_summary = result.reasoning_summary
        target.timestamp_resolution_seconds = resolution_seconds
        target.pass_count = pass_count
        target.provider_metadata = response.provider_metadata
        target.usage_metadata = response.usage_metadata
        target.latency_seconds = response.latency_seconds
        target.error = None
        target.completed_at = datetime.now(UTC)
