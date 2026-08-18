import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    MediaAsset,
    Note,
    RetrievalCandidate,
    RetrievalCandidateTemporalSegment,
    RetrievalRun,
    TemporalIndex,
)
from app.repositories.candidate_retrieval import (
    CandidateEvidenceRecord,
    CandidateRetrievalRepository,
)
from app.repositories.temporal_index import TemporalSearchHit
from app.services.embedding_provider import EmbeddingProvider
from app.services.temporal_index import TemporalPrerequisiteError, TemporalSearchService

logger = structlog.get_logger(__name__)

RETRIEVAL_STRATEGY = "semantic_temporal_v1"


class RetrievalPrerequisiteError(ValueError):
    pass


class RetrievalInProgressError(ValueError):
    pass


@dataclass(frozen=True)
class CandidateRetrievalConfig:
    hit_limit: int = 30
    max_candidates: int = 5
    min_semantic_score: float = 0.25
    max_segment_gap_seconds: float = 5.0
    context_before_seconds: float = 15.0
    context_after_seconds: float = 20.0
    max_candidate_duration_seconds: float = 120.0

    def validate(self) -> None:
        if self.hit_limit < 1:
            raise ValueError("hit_limit must be positive")
        if self.max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        if self.max_segment_gap_seconds < 0:
            raise ValueError("max_segment_gap_seconds must be nonnegative")
        if self.context_before_seconds < 0 or self.context_after_seconds < 0:
            raise ValueError("candidate context must be nonnegative")
        if self.max_candidate_duration_seconds <= 0:
            raise ValueError("max_candidate_duration_seconds must be positive")


@dataclass(frozen=True)
class RetrievalSubject:
    subject_key: str
    query: str
    note_id: UUID | None = None
    chronological_position: float | None = None


@dataclass(frozen=True)
class CandidateHit:
    hit: TemporalSearchHit
    hit_rank: int


@dataclass(frozen=True)
class CandidateDefinition:
    start_seconds: float
    end_seconds: float
    score: float
    score_components: dict[str, float | None]
    hits: tuple[CandidateHit, ...]
    cluster_start_seconds: float
    cluster_end_seconds: float


@dataclass(frozen=True)
class RetrievalResult:
    run: RetrievalRun
    candidates: list[RetrievalCandidate]
    created: bool


def normalize_query(text: str) -> str:
    return " ".join(text.split())


def retrieval_fingerprint(configuration: dict[str, Any]) -> str:
    canonical = json.dumps(configuration, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _candidate_score(
    hits: list[CandidateHit],
    cluster_start: float,
    cluster_end: float,
    media_duration: float,
    chronological_position: float | None,
) -> tuple[float, dict[str, float | None]]:
    scores = [item.hit.score for item in hits]
    semantic_mean = sum(scores) / len(scores)
    semantic_max = max(scores)
    covered = sum(
        item.hit.segment.end_seconds - item.hit.segment.start_seconds
        for item in hits
    )
    density = min(1.0, covered / max(cluster_end - cluster_start, 0.000001))
    chronological_prior: float | None = None
    if chronological_position is None:
        score = 0.60 * semantic_mean + 0.30 * semantic_max + 0.10 * density
    else:
        midpoint_position = (
            ((cluster_start + cluster_end) / 2.0) / media_duration
            if media_duration > 0
            else 0.0
        )
        chronological_prior = max(
            0.0,
            1.0 - abs(midpoint_position - chronological_position),
        )
        score = (
            0.55 * semantic_mean
            + 0.30 * semantic_max
            + 0.10 * density
            + 0.05 * chronological_prior
        )
    return score, {
        "semantic_mean": semantic_mean,
        "semantic_max": semantic_max,
        "density": density,
        "chronological_prior": chronological_prior,
    }


def _expanded_window(
    hits: list[CandidateHit],
    media_duration: float,
    config: CandidateRetrievalConfig,
) -> tuple[float, float]:
    cluster_start = hits[0].hit.segment.start_seconds
    cluster_end = hits[-1].hit.segment.end_seconds
    start = max(0.0, cluster_start - config.context_before_seconds)
    end = min(media_duration, cluster_end + config.context_after_seconds)
    if end - start <= config.max_candidate_duration_seconds:
        return start, end

    peak = max(hits, key=lambda item: (item.hit.score, -item.hit.segment.sequence))
    center = (peak.hit.segment.start_seconds + peak.hit.segment.end_seconds) / 2.0
    half = config.max_candidate_duration_seconds / 2.0
    start = max(0.0, center - half)
    end = min(media_duration, start + config.max_candidate_duration_seconds)
    start = max(0.0, end - config.max_candidate_duration_seconds)
    return start, end


def build_candidate_definitions(
    hits: list[TemporalSearchHit],
    media_duration: float,
    config: CandidateRetrievalConfig,
    *,
    chronological_position: float | None = None,
) -> list[CandidateDefinition]:
    config.validate()
    if media_duration <= 0:
        raise ValueError("media duration must be positive")

    ranked: dict[UUID, CandidateHit] = {}
    for hit_rank, hit in enumerate(hits, start=1):
        if hit.score < config.min_semantic_score:
            continue
        current = ranked.get(hit.segment.id)
        if current is None or hit.score > current.hit.score:
            ranked[hit.segment.id] = CandidateHit(hit, hit_rank)
    temporal_hits = sorted(
        ranked.values(),
        key=lambda item: (
            item.hit.segment.start_seconds,
            item.hit.segment.sequence,
        ),
    )
    if not temporal_hits:
        return []

    clusters: list[list[CandidateHit]] = []
    current_cluster: list[CandidateHit] = []
    for item in temporal_hits:
        if not current_cluster:
            current_cluster = [item]
            continue
        previous = current_cluster[-1]
        gap = item.hit.segment.start_seconds - previous.hit.segment.end_seconds
        projected_start = current_cluster[0].hit.segment.start_seconds
        projected_duration = (
            item.hit.segment.end_seconds
            - projected_start
            + config.context_before_seconds
            + config.context_after_seconds
        )
        if (
            gap <= config.max_segment_gap_seconds
            and projected_duration <= config.max_candidate_duration_seconds
        ):
            current_cluster.append(item)
        else:
            clusters.append(current_cluster)
            current_cluster = [item]
    clusters.append(current_cluster)

    definitions: list[CandidateDefinition] = []
    for cluster in clusters:
        cluster_start = cluster[0].hit.segment.start_seconds
        cluster_end = cluster[-1].hit.segment.end_seconds
        start, end = _expanded_window(cluster, media_duration, config)
        score, components = _candidate_score(
            cluster,
            cluster_start,
            cluster_end,
            media_duration,
            chronological_position,
        )
        definitions.append(
            CandidateDefinition(
                start_seconds=start,
                end_seconds=end,
                score=score,
                score_components=components,
                hits=tuple(sorted(cluster, key=lambda item: item.hit_rank)),
                cluster_start_seconds=cluster_start,
                cluster_end_seconds=cluster_end,
            )
        )
    definitions.sort(
        key=lambda item: (
            -item.score,
            item.start_seconds,
            item.end_seconds,
        )
    )
    return definitions[: config.max_candidates]


class CandidateRetriever:
    def __init__(self, db: AsyncSession, embedding_provider: EmbeddingProvider):
        self.db = db
        self.embedding_provider = embedding_provider

    async def retrieve_many(
        self,
        temporal_index_id: UUID,
        subjects: list[RetrievalSubject],
        config: CandidateRetrievalConfig,
        media_duration: float,
    ) -> dict[str, list[CandidateDefinition]]:
        active = [subject for subject in subjects if subject.query]
        if not active:
            return {subject.subject_key: [] for subject in subjects}
        hit_sets = await TemporalSearchService(
            self.db,
            self.embedding_provider,
        ).search_many(
            temporal_index_id,
            [subject.query for subject in active],
            config.hit_limit,
        )
        result: dict[str, list[CandidateDefinition]] = {
            subject.subject_key: [] for subject in subjects
        }
        for subject, hits in zip(active, hit_sets, strict=True):
            result[subject.subject_key] = build_candidate_definitions(
                hits,
                media_duration,
                config,
                chronological_position=subject.chronological_position,
            )
        return result


class CandidateRetrievalService:
    def __init__(self, db: AsyncSession, embedding_provider: EmbeddingProvider):
        self.db = db
        self.embedding_provider = embedding_provider
        self.repository = CandidateRetrievalRepository(db)

    async def retrieve_query(
        self,
        temporal_index_id: UUID,
        query: str,
        config: CandidateRetrievalConfig,
    ) -> RetrievalResult:
        normalized = normalize_query(query)
        if not normalized:
            raise ValueError("query must not be blank")
        return await self._execute(
            temporal_index_id,
            kind="query",
            subjects=[RetrievalSubject("query", normalized)],
            config=config,
            query_text=normalized,
        )

    async def retrieve_notes(
        self,
        temporal_index_id: UUID,
        note_ids: list[UUID] | None,
        config: CandidateRetrievalConfig,
    ) -> RetrievalResult:
        temporal_index, _ = await self._prerequisites(temporal_index_id)
        result = await self.db.execute(
            select(Note)
            .where(Note.session_id == temporal_index.session_id)
            .order_by(Note.order)
        )
        session_notes = list(result.scalars().all())
        by_id = {note.id: note for note in session_notes}
        if note_ids is None:
            notes = session_notes
        else:
            if len(set(note_ids)) != len(note_ids):
                raise RetrievalPrerequisiteError("note_ids must not contain duplicates")
            missing = [note_id for note_id in note_ids if note_id not in by_id]
            if missing:
                raise RetrievalPrerequisiteError(
                    "every note must belong to the temporal index session"
                )
            selected = set(note_ids)
            notes = [note for note in session_notes if note.id in selected]

        count = len(session_notes)
        subjects = [
            RetrievalSubject(
                subject_key=f"note:{note.id}",
                query=normalize_query(note.raw),
                note_id=note.id,
                chronological_position=(
                    (note.order - 1) / (count - 1) if count > 1 else 0.5
                ),
            )
            for note in notes
        ]
        return await self._execute(
            temporal_index_id,
            kind="notes",
            subjects=subjects,
            config=config,
            query_text=None,
        )

    async def _prerequisites(
        self,
        temporal_index_id: UUID,
    ) -> tuple[TemporalIndex, MediaAsset]:
        temporal_index = await self.db.get(TemporalIndex, temporal_index_id)
        if temporal_index is None:
            raise LookupError("temporal index not found")
        if temporal_index.status != "completed":
            raise TemporalPrerequisiteError(
                "temporal index must be completed before candidate retrieval"
            )
        if temporal_index.embedding_provider != self.embedding_provider.name:
            raise TemporalPrerequisiteError(
                "configured embedding provider is not available"
            )
        media = await self.db.get(MediaAsset, temporal_index.media_id)
        if media is None or media.duration_seconds is None or media.duration_seconds <= 0:
            raise RetrievalPrerequisiteError("media duration is required")
        return temporal_index, media

    def _configuration(
        self,
        temporal_index_id: UUID,
        kind: str,
        subjects: list[RetrievalSubject],
        config: CandidateRetrievalConfig,
    ) -> dict[str, Any]:
        return {
            "temporal_index_id": str(temporal_index_id),
            "kind": kind,
            "strategy": RETRIEVAL_STRATEGY,
            "subjects": [
                {
                    "subject_key": subject.subject_key,
                    "query": subject.query if kind == "query" else None,
                    "note_id": str(subject.note_id) if subject.note_id else None,
                }
                for subject in subjects
            ],
            "retrieval": asdict(config),
            "score_formula": (
                "query=.60*mean+.30*max+.10*density;"
                "notes=.55*mean+.30*max+.10*density+.05*chronological"
            ),
            "interval_semantics": "[start,end)",
        }

    async def _execute(
        self,
        temporal_index_id: UUID,
        *,
        kind: str,
        subjects: list[RetrievalSubject],
        config: CandidateRetrievalConfig,
        query_text: str | None,
    ) -> RetrievalResult:
        config.validate()
        temporal_index, media = await self._prerequisites(temporal_index_id)
        media_duration = media.duration_seconds
        assert media_duration is not None
        configuration = self._configuration(
            temporal_index_id,
            kind,
            subjects,
            config,
        )
        fingerprint = retrieval_fingerprint(configuration)
        existing = await self.repository.by_fingerprint(
            temporal_index_id,
            fingerprint,
        )
        created = False
        if existing is not None:
            if existing.status == "completed":
                candidates = list(await self.repository.candidates(existing.id))
                return RetrievalResult(existing, candidates, False)
            if existing.status in {"pending", "processing"}:
                raise RetrievalInProgressError("equivalent retrieval is already processing")
            run = existing
            await self.repository.clear_candidates(run.id)
        else:
            run = RetrievalRun(
                project_id=temporal_index.project_id,
                session_id=temporal_index.session_id,
                media_id=temporal_index.media_id,
                temporal_index_id=temporal_index.id,
                kind=kind,
                strategy=RETRIEVAL_STRATEGY,
                status="pending",
                query_text=query_text,
                configuration=configuration,
                configuration_fingerprint=fingerprint,
            )
            self.db.add(run)
            try:
                await self.db.flush()
                await self.db.commit()
            except IntegrityError as exc:
                await self.db.rollback()
                concurrent = await self.repository.by_fingerprint(
                    temporal_index_id,
                    fingerprint,
                )
                if concurrent is None:
                    raise exc
                if concurrent.status != "completed":
                    raise RetrievalInProgressError(
                        "equivalent retrieval is already processing"
                    ) from exc
                candidates = list(await self.repository.candidates(concurrent.id))
                return RetrievalResult(concurrent, candidates, False)
            created = True

        run_id = run.id
        log_context = {
            "project_id": str(temporal_index.project_id),
            "session_id": str(temporal_index.session_id),
            "media_id": str(temporal_index.media_id),
            "temporal_index_id": str(temporal_index.id),
            "retrieval_run_id": str(run_id),
            "strategy": RETRIEVAL_STRATEGY,
        }
        run.status = "processing"
        run.started_at = datetime.now(UTC)
        run.completed_at = None
        await self.db.commit()
        try:
            definitions = await CandidateRetriever(
                self.db,
                self.embedding_provider,
            ).retrieve_many(
                temporal_index.id,
                subjects,
                config,
                media_duration,
            )
            persisted: list[RetrievalCandidate] = []
            for subject in subjects:
                for rank, definition in enumerate(
                    definitions[subject.subject_key],
                    start=1,
                ):
                    candidate = RetrievalCandidate(
                        retrieval_run_id=run.id,
                        note_id=subject.note_id,
                        subject_key=subject.subject_key,
                        rank=rank,
                        start_seconds=definition.start_seconds,
                        end_seconds=definition.end_seconds,
                        duration_seconds=(
                            definition.end_seconds - definition.start_seconds
                        ),
                        score=definition.score,
                        score_components=definition.score_components,
                        candidate_metadata={
                            "cluster_start_seconds": (
                                definition.cluster_start_seconds
                            ),
                            "cluster_end_seconds": definition.cluster_end_seconds,
                            "context_before_seconds": config.context_before_seconds,
                            "context_after_seconds": config.context_after_seconds,
                            "max_candidate_duration_seconds": (
                                config.max_candidate_duration_seconds
                            ),
                        },
                    )
                    self.db.add(candidate)
                    await self.db.flush()
                    self.db.add_all(
                        [
                            RetrievalCandidateTemporalSegment(
                                retrieval_candidate_id=candidate.id,
                                temporal_segment_id=hit.hit.segment.id,
                                similarity_score=hit.hit.score,
                                hit_rank=hit.hit_rank,
                            )
                            for hit in definition.hits
                        ]
                    )
                    persisted.append(candidate)
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            await self.db.commit()
            await self.db.refresh(run)
            logger.info(
                "candidate_retrieval_completed",
                **log_context,
                candidate_count=len(persisted),
                subject_count=len(subjects),
            )
            return RetrievalResult(run, persisted, created)
        except Exception:
            await self.db.rollback()
            failed = await self.repository.get_run(run_id)
            if failed is not None:
                await self.repository.clear_candidates(failed.id)
                failed.status = "failed"
                failed.completed_at = datetime.now(UTC)
                await self.db.commit()
            logger.exception("candidate_retrieval_failed", **log_context)
            raise


class CandidateEvidenceService:
    def __init__(self, db: AsyncSession):
        self.repository = CandidateRetrievalRepository(db)

    async def get(self, candidate_id: UUID) -> CandidateEvidenceRecord:
        record = await self.repository.evidence(candidate_id)
        if record is None:
            raise LookupError("retrieval candidate not found")
        return record
