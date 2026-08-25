from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class ProjectOut(ORMModel):
    id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime


class NoteOut(ORMModel):
    id: UUID
    order: int
    raw: str


class MediaOut(ORMModel):
    id: UUID
    storage_uri: str
    original_filename: str
    content_type: str | None
    byte_size: int
    sha256: str
    duration_seconds: float | None
    format_name: str | None
    video_codec: str | None
    audio_codec: str | None
    fps: float | None
    width: int | None
    height: int | None
    probe_metadata: dict[str, Any] | None


class MediaAssetMetadataOut(ORMModel):
    """Safe public identity for source materialization; never exposes storage details."""

    id: UUID
    project_id: UUID
    original_filename: str
    content_type: str | None
    byte_size: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    duration_seconds: float | None = Field(default=None, gt=0)
    format_name: str | None
    video_codec: str | None
    audio_codec: str | None
    fps: float | None = Field(default=None, gt=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    created_at: datetime


class SessionSummary(ORMModel):
    id: UUID
    project_id: UUID
    media_id: UUID
    label: str
    status: str
    created_at: datetime


class SessionOut(SessionSummary):
    notes_filename: str
    raw_notes: str
    media: MediaOut
    notes: list[NoteOut]


class JobOut(ORMModel):
    id: UUID
    temporal_index_id: UUID | None
    alignment_run_id: UUID | None
    project_id: UUID
    session_id: UUID
    media_id: UUID
    transcription_id: UUID | None
    job_type: str
    visual_index_id: UUID | None
    status: str
    stage: str
    progress: float
    heartbeat_at: datetime | None
    attempt_count: int
    max_attempts: int
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SessionIngestOut(BaseModel):
    session: SessionOut
    job: JobOut
