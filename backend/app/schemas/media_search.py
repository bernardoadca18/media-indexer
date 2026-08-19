"""Media Search schemas for session-level and project-level search APIs."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.domain import ORMModel


class MediaSearchRequest(BaseModel):
    """Request for searching media within a session or project."""

    query: str = Field(min_length=1, max_length=2000, description="Search query text")
    max_results: int = Field(default=10, ge=1, le=100, description="Maximum results to return")
    min_score: float = Field(default=0.25, ge=0.0, le=1.0, description="Minimum relevance score")
    start_seconds: float | None = Field(
        default=None, ge=0.0, description="Filter by start time (session search only)"
    )
    end_seconds: float | None = Field(
        default=None, ge=0.0, description="Filter by end time (session search only)"
    )
    session_ids: list[UUID] | None = Field(
        default=None, description="Filter by specific sessions (project search only)"
    )
    exclude_session_ids: list[UUID] | None = Field(
        default=None, description="Exclude specific sessions (project search only)"
    )
    max_duration_seconds: float | None = Field(
        default=None, gt=0, description="Maximum duration filter"
    )
    temporal_index_id: UUID | None = Field(
        default=None, description="Explicit temporal index (advanced, optional)"
    )


class MediaSearchResult(BaseModel):
    """A single search result representing a footage range."""

    session_id: UUID
    media_id: UUID
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    score: float = Field(ge=0, le=1)
    rank: int = Field(ge=1)

    session_label: str = Field(description="Human-readable session label")
    media_filename: str = Field(description="Original media filename")

    temporal_index_id: UUID
    retrieval_candidate_id: UUID | None = None

    transcript_preview: str | None = Field(
        default=None, description="Brief transcript excerpt"
    )
    matched_note_ids: list[UUID] = Field(default_factory=list)

    provenance: dict[str, Any] = Field(
        default_factory=dict, description="Debugging provenance"
    )


class MediaSearchSessionSummary(BaseModel):
    """Summary of a searched session in project search results."""

    id: UUID
    label: str
    searched: bool
    result_count: int = 0
    skip_reason: str | None = None


class MediaSearchResponse(BaseModel):
    """Response for media search operation."""

    query: str
    scope: str = Field(description="'session' or 'project'")
    project_id: UUID
    session_id: UUID | None = None

    results: list[MediaSearchResult]

    searched_sessions: int = Field(description="Number of sessions searched")
    skipped_sessions: int = Field(description="Number of sessions skipped")

    session_summaries: list[MediaSearchSessionSummary] = Field(
        default_factory=list, description="Per-session summary (project search only)"
    )

    strategy: str = Field(default="media_search_v1", description="Search strategy identifier")

    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int

    created_at: datetime


class TemporalIndexResolution(BaseModel):
    """Information about which temporal index was selected."""

    temporal_index_id: UUID
    session_id: UUID
    selection_reason: str = Field(
        description="Why this index was selected: 'explicit', 'default_latest', etc."
    )
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    compatibility_signature: str = Field(
        description="Hash signature for cross-session compatibility"
    )
