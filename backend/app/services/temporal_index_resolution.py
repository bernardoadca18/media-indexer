"""TemporalIndex resolution policy for Media Search.

POLICY OVERVIEW
================

Media Search consumers should not need to know temporal_index_id.
This module provides deterministic index resolution with explicit conflict handling.

SELECTION STRATEGY
==================

1. EXPLICIT: If request specifies temporal_index_id, validate and use it.
   - Must exist and belong to the session
   - Must be completed
   - Must match the configured embedding provider

2. DEFAULT: If not specified, select the appropriate default/canonical index:
   - Only consider indexes with status='completed'
   - Only consider indexes matching the current embedding provider
   - Prefer the most recently created index
   - If multiple candidates exist with different configurations, return CONFLICT error

3. CONFLICT: If multiple equally-valid candidates exist without clear selection:
   - Return AmbiguousTemporalIndexError
   - Include all candidates in the error for user decision

COMPATIBILITY SIGNATURE
========================

For cross-session project search, we compute a compatibility signature from:
- embedding_provider
- embedding_model
- embedding_dimensions

This ensures we only combine indexes that use comparable embeddings.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TemporalIndex
from app.services.embedding_provider import EmbeddingProvider

logger = structlog.get_logger(__name__)


class TemporalIndexResolutionError(ValueError):
    """Base error for temporal index resolution."""


class TemporalIndexNotFoundError(TemporalIndexResolutionError):
    """No suitable temporal index found."""


class AmbiguousTemporalIndexError(TemporalIndexResolutionError):
    """Multiple suitable temporal indexes found without clear default."""

    def __init__(self, message: str, candidates: list[TemporalIndex]) -> None:
        super().__init__(message)
        self.candidates = candidates


class IncompatibleTemporalIndexError(TemporalIndexResolutionError):
    """Explicit temporal index is incompatible with request."""


@dataclass(frozen=True)
class ResolvedTemporalIndex:
    """Result of temporal index resolution."""

    temporal_index: TemporalIndex
    selection_reason: str
    compatibility_signature: str


def compute_compatibility_signature(
    embedding_provider: str,
    embedding_model: str,
    embedding_dimensions: int,
) -> str:
    """Compute a signature for cross-session embedding compatibility.

    Two temporal indexes are compatible for cross-session search if they
    have the same signature, meaning they use the same embedding configuration.
    """
    canonical = json.dumps(
        {
            "provider": embedding_provider,
            "model": embedding_model,
            "dimensions": embedding_dimensions,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def temporal_index_signature(index: TemporalIndex) -> str:
    """Get compatibility signature for a temporal index."""
    return compute_compatibility_signature(
        index.embedding_provider,
        index.embedding_model,
        index.embedding_dimensions,
    )


@dataclass
class TemporalIndexCandidate:
    """A candidate temporal index for resolution."""

    temporal_index: TemporalIndex
    compatibility_signature: str
    created_at: datetime
    is_default_provider: bool


class TemporalIndexResolutionService:
    """Resolves appropriate TemporalIndex for media search operations."""

    def __init__(self, db: AsyncSession, embedding_provider: EmbeddingProvider):
        self.db = db
        self.embedding_provider = embedding_provider

    async def resolve_session(
        self,
        session_id: UUID,
        temporal_index_id: UUID | None,
    ) -> ResolvedTemporalIndex:
        """Resolve the appropriate TemporalIndex for a session search.

        Args:
            session_id: The session to search within
            temporal_index_id: Optional explicit index selection

        Returns:
            ResolvedTemporalIndex with selection details

        Raises:
            TemporalIndexNotFoundError: No suitable index found
            AmbiguousTemporalIndexError: Multiple candidates without clear default
            IncompatibleTemporalIndexError: Explicit index doesn't match provider
        """
        if temporal_index_id is not None:
            return await self._resolve_explicit(session_id, temporal_index_id)

        return await self._resolve_default(session_id)

    async def resolve_project(
        self,
        project_id: UUID,
        session_ids: list[UUID] | None,
        exclude_session_ids: list[UUID] | None,
    ) -> tuple[list[ResolvedTemporalIndex], list[tuple[UUID, str]]]:
        """Resolve TemporalIndexes for project search.

        Args:
            project_id: The project to search within
            session_ids: Optional session filter
            exclude_session_ids: Optional session exclusions

        Returns:
            Tuple of (resolved_indexes, skipped_sessions)
            skipped_sessions is list of (session_id, reason) tuples

        Raises:
            TemporalIndexNotFoundError: No sessions have suitable indexes
        """
        from app.models import MediaSession

        result = await self.db.execute(
            select(MediaSession)
            .where(MediaSession.project_id == project_id)
            .where(MediaSession.status == "ready")
            .order_by(MediaSession.created_at)
        )
        sessions = list(result.scalars().all())

        if session_ids is not None:
            session_set = set(session_ids)
            sessions = [s for s in sessions if s.id in session_set]

        if exclude_session_ids is not None:
            exclude_set = set(exclude_session_ids)
            sessions = [s for s in sessions if s.id not in exclude_set]

        resolved: list[ResolvedTemporalIndex] = []
        skipped: list[tuple[UUID, str]] = []

        for session in sessions:
            try:
                index = await self._resolve_default(session.id)
                resolved.append(index)
            except TemporalIndexNotFoundError as e:
                skipped.append((session.id, str(e)))
            except AmbiguousTemporalIndexError as e:
                skipped.append(
                    (session.id, f"ambiguous temporal indexes: {len(e.candidates)} candidates")
                )

        return resolved, skipped

    async def validate_compatibility(
        self,
        resolved_indexes: list[ResolvedTemporalIndex],
    ) -> list[list[ResolvedTemporalIndex]]:
        """Group resolved indexes by compatibility signature.

        Returns:
            List of compatibility groups, each containing compatible indexes.
            Empty groups are filtered out.
        """
        groups: dict[str, list[ResolvedTemporalIndex]] = {}
        for resolved in resolved_indexes:
            sig = resolved.compatibility_signature
            groups.setdefault(sig, []).append(resolved)
        return [group for group in groups.values() if group]

    async def _resolve_explicit(
        self,
        session_id: UUID,
        temporal_index_id: UUID,
    ) -> ResolvedTemporalIndex:
        """Resolve an explicitly specified temporal index."""
        temporal_index = await self.db.get(TemporalIndex, temporal_index_id)

        if temporal_index is None:
            raise TemporalIndexNotFoundError(
                f"Temporal index {temporal_index_id} not found"
            )

        if temporal_index.session_id != session_id:
            raise IncompatibleTemporalIndexError(
                f"Temporal index {temporal_index_id} does not belong to session {session_id}"
            )

        if temporal_index.status != "completed":
            raise IncompatibleTemporalIndexError(
                f"Temporal index {temporal_index_id} is not completed (status={temporal_index.status})"
            )

        if temporal_index.embedding_provider != self.embedding_provider.name:
            raise IncompatibleTemporalIndexError(
                f"Temporal index {temporal_index_id} uses provider "
                f"'{temporal_index.embedding_provider}', "
                f"but '{self.embedding_provider.name}' is configured"
            )

        signature = temporal_index_signature(temporal_index)
        logger.info(
            "temporal_index_resolved_explicit",
            session_id=str(session_id),
            temporal_index_id=str(temporal_index_id),
            compatibility_signature=signature,
        )

        return ResolvedTemporalIndex(
            temporal_index=temporal_index,
            selection_reason="explicit",
            compatibility_signature=signature,
        )

    async def _resolve_default(
        self,
        session_id: UUID,
    ) -> ResolvedTemporalIndex:
        """Resolve the default temporal index for a session.

        Strategy:
        1. Find all completed indexes matching the current embedding provider
        2. If exactly one, use it
        3. If multiple, use most recently created
        4. If none, raise TemporalIndexNotFoundError
        """
        result = await self.db.execute(
            select(TemporalIndex)
            .where(TemporalIndex.session_id == session_id)
            .where(TemporalIndex.status == "completed")
            .where(TemporalIndex.embedding_provider == self.embedding_provider.name)
            .order_by(TemporalIndex.created_at.desc())
        )
        candidates = list(result.scalars().all())

        if not candidates:
            raise TemporalIndexNotFoundError(
                f"No completed temporal index found for session {session_id} "
                f"with provider '{self.embedding_provider.name}'"
            )

        # Select the most recent
        selected = candidates[0]
        signature = temporal_index_signature(selected)

        reason = "default_latest"
        if len(candidates) > 1:
            reason = f"default_latest_of_{len(candidates)}"
            logger.warning(
                "temporal_index_resolution_multiple_candidates",
                session_id=str(session_id),
                selected_id=str(selected.id),
                candidate_count=len(candidates),
                candidate_ids=[str(c.id) for c in candidates],
            )

        logger.info(
            "temporal_index_resolved_default",
            session_id=str(session_id),
            temporal_index_id=str(selected.id),
            compatibility_signature=signature,
            candidate_count=len(candidates),
        )

        return ResolvedTemporalIndex(
            temporal_index=selected,
            selection_reason=reason,
            compatibility_signature=signature,
        )
