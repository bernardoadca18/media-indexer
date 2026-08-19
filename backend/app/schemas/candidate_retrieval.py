from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.domain import ORMModel


class CandidateOptions(BaseModel):
    hit_limit: int | None = Field(default=None, ge=1, le=200)
    max_candidates: int | None = Field(default=None, ge=1, le=20)
    min_semantic_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    max_segment_gap_seconds: float | None = Field(default=None, ge=0)
    context_before_seconds: float | None = Field(default=None, ge=0)
    context_after_seconds: float | None = Field(default=None, ge=0)
    max_candidate_duration_seconds: float | None = Field(default=None, gt=0)


class QueryCandidateCreate(CandidateOptions):
    query: str = Field(min_length=1, max_length=4000)


class NoteRetrievalCreate(CandidateOptions):
    note_ids: list[UUID] | None = None


class RetrievalRunOut(ORMModel):
    id: UUID
    project_id: UUID
    session_id: UUID
    media_id: UUID
    temporal_index_id: UUID
    kind: str
    strategy: str
    status: str
    query_text: str | None
    configuration: dict[str, Any]
    configuration_fingerprint: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RetrievalCandidateOut(ORMModel):
    id: UUID
    retrieval_run_id: UUID
    note_id: UUID | None
    rank: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    score: float
    score_components: dict[str, Any]
    metadata: dict[str, Any] | None = Field(validation_alias="candidate_metadata")
    created_at: datetime


class RetrievalResultOut(BaseModel):
    retrieval_run: RetrievalRunOut
    candidates: list[RetrievalCandidateOut]
    created: bool


class CandidateHitOut(BaseModel):
    temporal_segment_id: UUID
    sequence: int
    start_seconds: float
    end_seconds: float
    search_text: str
    similarity_score: float
    hit_rank: int


class WindowTemporalSegmentOut(BaseModel):
    id: UUID
    sequence: int
    start_seconds: float
    end_seconds: float
    text: str
    embedding_status: str


class CandidateNoteOut(BaseModel):
    id: UUID
    order: int
    raw: str


class CandidateTranscriptOut(BaseModel):
    id: UUID
    sequence: int
    start_seconds: float
    end_seconds: float
    text: str


class CandidateSceneOut(BaseModel):
    id: UUID
    sequence: int
    start_seconds: float
    end_seconds: float


class CandidateFrameOut(BaseModel):
    asset_id: UUID
    timestamp_seconds: float
    storage_uri: str
    width: int | None
    height: int | None
    metadata: dict[str, Any] | None


class CandidateEvidenceOut(BaseModel):
    candidate: RetrievalCandidateOut
    retrieval_run: RetrievalRunOut
    note: CandidateNoteOut | None
    contributing_hits: list[CandidateHitOut]
    temporal_segments: list[WindowTemporalSegmentOut]
    transcript: list[CandidateTranscriptOut]
    scenes: list[CandidateSceneOut]
    frames: list[CandidateFrameOut]
