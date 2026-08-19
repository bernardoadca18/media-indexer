"""Media Search service for session-level and project-level search.

This service provides the public API for searching media footage,
hiding internal details like TemporalIndex IDs, RetrievalRun, embeddings, and pgvector.

KEY DESIGN DECISIONS
=====================

1. QUERY EMBEDDING REUSE
   For project search, we embed the query ONCE per compatibility group,
   not once per session. This is critical for cost and latency.

2. REUSE OF CANDIDATE RETRIEVAL
   Media Search reuses the existing CandidateRetriever logic.
   It does NOT duplicate vector search or scoring.

3. NO AUTOMATIC ALIGNMENT
   Media Search performs deterministic retrieval only.
   It does not invoke multimodal alignment by default.
   Alignment is optional enrichment when available.

4. SYNCHRONOUS API
   Media Search is synchronous for MVP.
   Embedding + vector search should be fast for expected scale.

5. COMPATIBILITY GROUPING
   Project search groups sessions by compatibility signature
   (provider + model + dimensions) and only combines compatible indexes.
"""

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MediaAsset, MediaSession
from app.schemas.media_search import (
    MediaSearchRequest,
    MediaSearchResponse,
    MediaSearchResult,
    MediaSearchSessionSummary,
)
from app.services.candidate_retrieval import (
    CandidateDefinition,
    CandidateRetrievalConfig,
    CandidateRetriever,
    RetrievalSubject,
    build_candidate_definitions,
)
from app.services.embedding_provider import EmbeddingProvider
from app.services.temporal_index_resolution import (
    ResolvedTemporalIndex,
    TemporalIndexResolutionError,
    TemporalIndexResolutionService,
)

logger = structlog.get_logger(__name__)

MEDIA_SEARCH_STRATEGY = "media_search_v1"


class MediaSearchError(ValueError):
    """Base error for media search operations."""


class MediaSearchQueryError(MediaSearchError):
    """Invalid search query."""


class MediaSearchNoResultsError(MediaSearchError):
    """Search completed with zero results (not an error, just informational)."""


def normalize_search_query(query: str) -> str:
    """Normalize a search query.

    - Strip leading/trailing whitespace
    - Collapse multiple whitespace to single space
    - Reject empty queries
    """
    normalized = " ".join(query.split())
    if not normalized:
        raise MediaSearchQueryError("query must not be empty after normalization")
    if len(normalized) > 2000:
        raise MediaSearchQueryError("query exceeds maximum length of 2000 characters")
    return normalized


def _filter_by_time_range(
    definitions: list[CandidateDefinition],
    start_seconds: float | None,
    end_seconds: float | None,
) -> list[CandidateDefinition]:
    """Filter candidate definitions by time range.

    A candidate overlaps the range if:
    - candidate.end > start_seconds (if specified)
    - candidate.start < end_seconds (if specified)
    """
    if start_seconds is None and end_seconds is None:
        return definitions

    filtered = []
    for definition in definitions:
        if start_seconds is not None and definition.end_seconds <= start_seconds:
            continue
        if end_seconds is not None and definition.start_seconds >= end_seconds:
            continue
        filtered.append(definition)
    return filtered


def _filter_by_duration(
    definitions: list[CandidateDefinition],
    max_duration_seconds: float | None,
) -> list[CandidateDefinition]:
    """Filter candidates by maximum duration."""
    if max_duration_seconds is None:
        return definitions
    return [d for d in definitions if (d.end_seconds - d.start_seconds) <= max_duration_seconds]


def _deduplicate_candidates(
    results: list[MediaSearchResult],
    overlap_threshold: float = 0.5,
) -> list[MediaSearchResult]:
    """Deduplicate results with significant temporal overlap.

    For results from the same session with >50% overlap, keep the higher-scored one.
    This is important for project search where multiple sessions might return
    similar footage ranges.
    """
    if len(results) <= 1:
        return results

    # Group by session
    by_session: dict[UUID, list[MediaSearchResult]] = {}
    for result in results:
        by_session.setdefault(result.session_id, []).append(result)

    deduplicated: list[MediaSearchResult] = []
    for session_results in by_session.values():
        # Sort by score descending
        sorted_results = sorted(session_results, key=lambda r: (-r.score, r.start_seconds))
        kept: list[MediaSearchResult] = []

        for candidate in sorted_results:
            # Check if this candidate overlaps significantly with any kept result
            should_keep = True
            for existing in kept:
                # Calculate overlap
                overlap_start = max(candidate.start_seconds, existing.start_seconds)
                overlap_end = min(candidate.end_seconds, existing.end_seconds)

                if overlap_start < overlap_end:
                    overlap_duration = overlap_end - overlap_start
                    candidate_duration = candidate.end_seconds - candidate.start_seconds
                    existing_duration = existing.end_seconds - existing.start_seconds

                    # If overlap > 50% of either candidate's duration, skip it
                    if (
                        overlap_duration / candidate_duration > overlap_threshold
                        or overlap_duration / existing_duration > overlap_threshold
                    ):
                        should_keep = False
                        break

            if should_keep:
                kept.append(candidate)

        deduplicated.extend(kept)

    return deduplicated


class MediaSearchService:
    """Service for searching media footage across sessions and projects."""

    def __init__(
        self,
        db: AsyncSession,
        embedding_provider: EmbeddingProvider,
    ):
        self.db = db
        self.embedding_provider = embedding_provider
        self.resolution_service = TemporalIndexResolutionService(db, embedding_provider)

    async def search_session(
        self,
        session_id: UUID,
        request: MediaSearchRequest,
    ) -> MediaSearchResponse:
        """Search for footage within a single session.

        This is a synchronous operation that:
        1. Resolves the appropriate TemporalIndex
        2. Embeds the query (once)
        3. Performs candidate retrieval
        4. Formats results

        Args:
            session_id: The session to search within
            request: Search parameters

        Returns:
            MediaSearchResponse with ranked results

        Raises:
            LookupError: Session not found
            MediaSearchError: Invalid parameters or no suitable index
        """
        # Validate session exists
        session = await self.db.get(MediaSession, session_id)
        if session is None:
            raise LookupError("session not found")

        normalized_query = normalize_search_query(request.query)

        # Validate time range filter
        if request.start_seconds is not None and request.end_seconds is not None:
            if request.end_seconds <= request.start_seconds:
                raise MediaSearchQueryError("end_seconds must be greater than start_seconds")

        # Resolve temporal index
        try:
            resolved = await self.resolution_service.resolve_session(
                session_id,
                request.temporal_index_id,
            )
        except TemporalIndexResolutionError as e:
            raise MediaSearchError(str(e)) from e

        temporal_index = resolved.temporal_index

        # Get media duration for retrieval
        media = await self.db.get(MediaAsset, temporal_index.media_id)
        if media is None or media.duration_seconds is None:
            raise MediaSearchError("media duration not available")
        media_duration = media.duration_seconds

        # Build retrieval config
        config = CandidateRetrievalConfig(
            hit_limit=30,
            max_candidates=request.max_results * 2,  # Fetch more for filtering
            min_semantic_score=request.min_score,
            max_segment_gap_seconds=5.0,
            context_before_seconds=15.0,
            context_after_seconds=20.0,
            max_candidate_duration_seconds=request.max_duration_seconds or 120.0,
        )

        # Embed query and retrieve candidates
        # We use CandidateRetriever directly to reuse logic
        retriever = CandidateRetriever(self.db, self.embedding_provider)

        subject = RetrievalSubject(
            subject_key="query",
            query=normalized_query,
        )

        definitions_by_key = await retriever.retrieve_many(
            temporal_index.id,
            [subject],
            config,
            media_duration,
        )
        definitions = definitions_by_key["query"]

        # Apply filters
        definitions = _filter_by_time_range(
            definitions,
            request.start_seconds,
            request.end_seconds,
        )
        definitions = _filter_by_duration(definitions, request.max_duration_seconds)

        # Limit results
        definitions = definitions[: request.max_results]

        # Build results
        results = await self._build_results(
            definitions,
            resolved,
            session,
            media,
        )

        # Re-rank globally by score (already sorted, but ensure consistency)
        results.sort(key=lambda r: (-r.score, r.session_id, r.start_seconds))

        logger.info(
            "media_search_session_completed",
            session_id=str(session_id),
            query=normalized_query,
            result_count=len(results),
            temporal_index_id=str(temporal_index.id),
            compatibility_signature=resolved.compatibility_signature,
        )

        return MediaSearchResponse(
            query=normalized_query,
            scope="session",
            project_id=session.project_id,
            session_id=session_id,
            results=results,
            searched_sessions=1,
            skipped_sessions=0,
            session_summaries=[
                MediaSearchSessionSummary(
                    id=session_id,
                    label=session.label,
                    searched=True,
                    result_count=len(results),
                )
            ],
            strategy=MEDIA_SEARCH_STRATEGY,
            embedding_provider=temporal_index.embedding_provider,
            embedding_model=temporal_index.embedding_model,
            embedding_dimensions=temporal_index.embedding_dimensions,
            created_at=datetime.now(UTC),
        )

    async def search_project(
        self,
        project_id: UUID,
        request: MediaSearchRequest,
    ) -> MediaSearchResponse:
        """Search for footage across multiple sessions in a project.

        This operation:
        1. Lists eligible sessions
        2. Resolves TemporalIndex for each session
        3. Groups sessions by compatibility signature
        4. Embeds query ONCE per compatibility group
        5. Performs retrieval on each compatible index
        6. Aggregates and globally ranks results

        Args:
            project_id: The project to search within
            request: Search parameters

        Returns:
            MediaSearchResponse with globally ranked results

        Raises:
            LookupError: Project not found
            MediaSearchError: Invalid parameters or no suitable indexes
        """
        from app.models import MediaProject

        # Validate project exists
        project = await self.db.get(MediaProject, project_id)
        if project is None:
            raise LookupError("project not found")

        normalized_query = normalize_search_query(request.query)

        # Resolve temporal indexes for all eligible sessions
        try:
            resolved_indexes, skipped = await self.resolution_service.resolve_project(
                project_id,
                request.session_ids,
                request.exclude_session_ids,
            )
        except TemporalIndexResolutionError as e:
            raise MediaSearchError(str(e)) from e

        if not resolved_indexes:
            raise MediaSearchError(
                f"No sessions with completed temporal indexes found in project {project_id}"
            )

        # Group by compatibility signature
        compatibility_groups = await self.resolution_service.validate_compatibility(
            resolved_indexes
        )

        if not compatibility_groups:
            raise MediaSearchError("No compatible temporal indexes found")

        # Log compatibility analysis
        logger.info(
            "media_search_project_compatibility",
            project_id=str(project_id),
            query=normalized_query,
            resolved_count=len(resolved_indexes),
            compatibility_groups=len(compatibility_groups),
            group_sizes=[len(g) for g in compatibility_groups],
        )

        # Embed query ONCE per compatibility group
        # This is the key optimization for cost/latency
        query_embeddings: dict[str, list[float]] = {}

        for group in compatibility_groups:
            # All indexes in a group have the same signature
            signature = group[0].compatibility_signature
            temporal_index = group[0].temporal_index

            # Embed the query for this compatibility group
            vectors = await self.embedding_provider.embed(
                [normalized_query],
                model=temporal_index.embedding_model,
                expected_dimensions=temporal_index.embedding_dimensions,
            )
            query_embeddings[signature] = vectors[0]
            logger.info(
                "media_search_query_embedded",
                project_id=str(project_id),
                compatibility_signature=signature,
                sessions_in_group=len(group),
            )

        # Perform retrieval using precomputed embeddings
        all_results: list[MediaSearchResult] = []
        session_summaries: list[MediaSearchSessionSummary] = []

        for group in compatibility_groups:
            signature = group[0].compatibility_signature
            query_vector = query_embeddings[signature]

            for resolved in group:
                try:
                    results = await self._search_with_embedding(
                        resolved,
                        query_vector,
                        normalized_query,
                        request,
                    )
                    all_results.extend(results)

                    session_summaries.append(
                        MediaSearchSessionSummary(
                            id=resolved.temporal_index.session_id,
                            label="",  # Will be filled later
                            searched=True,
                            result_count=len(results),
                        )
                    )
                except Exception as e:
                    logger.exception(
                        "media_search_session_failed",
                        session_id=str(resolved.temporal_index.session_id),
                        error=str(e),
                    )
                    session_summaries.append(
                        MediaSearchSessionSummary(
                            id=resolved.temporal_index.session_id,
                            label="",
                            searched=False,
                            result_count=0,
                            skip_reason=str(e),
                        )
                    )

        # Fill in session labels
        session_ids = {s.id for s in session_summaries}
        sessions_by_id = await self._load_sessions(session_ids)

        for summary in session_summaries:
            if summary.id in sessions_by_id:
                summary.label = sessions_by_id[summary.id].label

        # Add skipped sessions to summaries
        for session_id, reason in skipped:
            session = sessions_by_id.get(session_id)
            session_summaries.append(
                MediaSearchSessionSummary(
                    id=session_id,
                    label=session.label if session else "",
                    searched=False,
                    result_count=0,
                    skip_reason=reason,
                )
            )

        # Deduplicate results (within same session)
        all_results = _deduplicate_candidates(all_results)

        # Global ranking by score
        all_results.sort(key=lambda r: (-r.score, r.session_id, r.start_seconds))

        # Apply global limit
        all_results = all_results[: request.max_results]

        # Reassign global ranks
        for rank, result in enumerate(all_results, start=1):
            result.rank = rank

        # Determine embedding info from the first group (arbitrary but consistent)
        first_index = compatibility_groups[0][0].temporal_index

        logger.info(
            "media_search_project_completed",
            project_id=str(project_id),
            query=normalized_query,
            result_count=len(all_results),
            searched_sessions=len(resolved_indexes),
            skipped_sessions=len(skipped),
            compatibility_groups=len(compatibility_groups),
        )

        return MediaSearchResponse(
            query=normalized_query,
            scope="project",
            project_id=project_id,
            session_id=None,
            results=all_results,
            searched_sessions=len(resolved_indexes),
            skipped_sessions=len(skipped),
            session_summaries=session_summaries,
            strategy=MEDIA_SEARCH_STRATEGY,
            embedding_provider=first_index.embedding_provider,
            embedding_model=first_index.embedding_model,
            embedding_dimensions=first_index.embedding_dimensions,
            created_at=datetime.now(UTC),
        )

    async def _search_with_embedding(
        self,
        resolved: ResolvedTemporalIndex,
        query_embedding: list[float],
        query: str,
        request: MediaSearchRequest,
    ) -> list[MediaSearchResult]:
        """Search a single temporal index using a precomputed query embedding.

        This reuses TemporalSearchService for the actual vector search,
        then builds candidate definitions from the hits.
        """
        temporal_index = resolved.temporal_index

        # Get media
        media = await self.db.get(MediaAsset, temporal_index.media_id)
        if media is None or media.duration_seconds is None:
            return []
        media_duration = media.duration_seconds

        # Get session
        session = await self.db.get(MediaSession, temporal_index.session_id)
        if session is None:
            return []

        # Perform vector search with precomputed embedding
        from app.repositories.temporal_index import TemporalIndexRepository

        repository = TemporalIndexRepository(self.db)
        hits = await repository.search(temporal_index.id, query_embedding, limit=30)

        # Build candidate definitions from hits
        config = CandidateRetrievalConfig(
            hit_limit=30,
            max_candidates=request.max_results * 2,
            min_semantic_score=request.min_score,
            max_segment_gap_seconds=5.0,
            context_before_seconds=15.0,
            context_after_seconds=20.0,
            max_candidate_duration_seconds=request.max_duration_seconds or 120.0,
        )

        definitions = build_candidate_definitions(
            hits,
            media_duration,
            config,
        )

        # Apply filters
        definitions = _filter_by_time_range(
            definitions,
            request.start_seconds,
            request.end_seconds,
        )
        definitions = _filter_by_duration(definitions, request.max_duration_seconds)
        definitions = definitions[: request.max_results]

        return await self._build_results(definitions, resolved, session, media)

    async def _build_results(
        self,
        definitions: list[CandidateDefinition],
        resolved: ResolvedTemporalIndex,
        session: MediaSession,
        media: MediaAsset,
    ) -> list[MediaSearchResult]:
        """Build MediaSearchResult objects from candidate definitions."""
        results: list[MediaSearchResult] = []

        for rank, definition in enumerate(definitions, start=1):
            result = MediaSearchResult(
                session_id=session.id,
                media_id=media.id,
                start_seconds=definition.start_seconds,
                end_seconds=definition.end_seconds,
                duration_seconds=definition.end_seconds - definition.start_seconds,
                score=definition.score,
                rank=rank,
                session_label=session.label,
                media_filename=media.original_filename,
                temporal_index_id=resolved.temporal_index.id,
                retrieval_candidate_id=None,  # Not persisted in this path
                transcript_preview=await self._get_transcript_preview(
                    resolved.temporal_index.id,
                    definition.start_seconds,
                    definition.end_seconds,
                ),
                matched_note_ids=[],  # Would require alignment data
                provenance={
                    "compatibility_signature": resolved.compatibility_signature,
                    "selection_reason": resolved.selection_reason,
                    "score_components": definition.score_components,
                    "cluster_start_seconds": definition.cluster_start_seconds,
                    "cluster_end_seconds": definition.cluster_end_seconds,
                },
            )
            results.append(result)

        return results

    async def _get_transcript_preview(
        self,
        temporal_index_id: UUID,
        start_seconds: float,
        end_seconds: float,
        max_length: int = 200,
    ) -> str | None:
        """Get a brief transcript preview for a time range."""
        from app.models import TemporalSegment

        result = await self.db.execute(
            TemporalSegment.__table__.select()
            .where(TemporalSegment.temporal_index_id == temporal_index_id)
            .where(TemporalSegment.end_seconds > start_seconds)
            .where(TemporalSegment.start_seconds < end_seconds)
            .where(TemporalSegment.text != "")
            .order_by(TemporalSegment.sequence)
            .limit(3)
        )

        segments = list(result.fetchall())
        if not segments:
            return None

        texts = [seg.text for seg in segments if seg.text]
        if not texts:
            return None

        combined = " ".join(texts)
        if len(combined) > max_length:
            combined = combined[:max_length].rsplit(" ", 1)[0] + "..."

        return combined

    async def _load_sessions(
        self,
        session_ids: set[UUID],
    ) -> dict[UUID, MediaSession]:
        """Load multiple sessions by ID."""
        from sqlalchemy import select

        result = await self.db.execute(select(MediaSession).where(MediaSession.id.in_(session_ids)))
        return {session.id: session for session in result.scalars().all()}
