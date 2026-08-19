from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.domain import JobOut, ORMModel


class AlignmentCreate(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=200)
    candidate_ids: list[UUID] | None = Field(default=None, min_length=1)
    frame_interval_seconds: float | None = Field(default=None, gt=0)
    max_frames: int | None = Field(default=None, ge=1, le=300)
    frame_width: int | None = Field(default=None, ge=64, le=4096)
    jpeg_quality: int | None = Field(default=None, ge=1, le=100)
    max_transcript_characters: int | None = Field(default=None, ge=1, le=100000)
    refinement_enabled: bool | None = None
    early_stop_confidence: float | None = Field(default=None, ge=0, le=1)


class AlignmentRunOut(ORMModel):
    id: UUID
    project_id: UUID
    session_id: UUID
    media_id: UUID
    retrieval_run_id: UUID
    provider: str
    model: str
    strategy: str
    prompt_version: str
    status: str
    configuration: dict[str, Any]
    configuration_fingerprint: str
    provider_metadata: dict[str, Any] | None
    usage_metadata: dict[str, Any] | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AlignmentSubjectOut(ORMModel):
    id: UUID
    alignment_run_id: UUID
    subject_key: str
    note_id: UUID | None
    observation: str
    status: str
    selected_candidate_alignment_id: UUID | None
    created_at: datetime


class CandidateAlignmentOut(ORMModel):
    id: UUID
    alignment_run_id: UUID
    alignment_subject_id: UUID
    retrieval_candidate_id: UUID
    note_id: UUID | None
    retrieval_rank: int
    status: str
    start_seconds: float | None
    end_seconds: float | None
    confidence: float | None
    anchors: list[dict[str, Any]]
    reasoning_summary: str | None
    timestamp_resolution_seconds: float | None
    pass_count: int
    provider_metadata: dict[str, Any] | None
    usage_metadata: dict[str, Any] | None
    latency_seconds: float | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    started_at: datetime | None
    completed_at: datetime | None


class AlignmentAcceptedOut(BaseModel):
    alignment_run: AlignmentRunOut
    job: JobOut
    created: bool


class AlignmentRunDetailOut(BaseModel):
    alignment_run: AlignmentRunOut
    subjects: list[AlignmentSubjectOut]


class CandidateAlignmentPageOut(BaseModel):
    items: list[CandidateAlignmentOut]
    total: int
    offset: int
    limit: int
