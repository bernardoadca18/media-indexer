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
    start_seconds: float | None = None
    end_seconds: float | None = None
    description: str | None = None
    tags: list[str] | None = None


class StructuredNoteIn(BaseModel):
    """Schema for creating/updating structured timeline notes."""

    order: int = Field(ge=1)
    raw: str = Field(min_length=1)
    start_seconds: float | None = Field(default=None, ge=0)
    end_seconds: float | None = Field(default=None)
    description: str | None = None
    tags: list[str] | None = None

    def model_post_init(self, context: Any) -> None:
        if self.start_seconds is not None and self.end_seconds is not None:
            if self.end_seconds <= self.start_seconds:
                raise ValueError("end_seconds must be greater than start_seconds")


class TimelineNoteObservation(BaseModel):
    """Single observation for bulk import."""

    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    description: str = Field(min_length=1)
    tags: list[str] | None = None

    def model_post_init(self, context: Any) -> None:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("end_seconds must be greater than start_seconds")


class TimelineNotesImportRequest(BaseModel):
    """Bulk import request for timeline notes."""

    version: str = Field(pattern="^timeline_notes_v1$")
    provenance: str = Field(default="manual_import", min_length=1)
    observations: list[TimelineNoteObservation] = Field(min_length=1)


class TimelineNotesImportResult(BaseModel):
    """Result of bulk timeline notes import."""

    imported_count: int
    skipped_count: int
    errors: list[str] | None = None


class TimelineNotesTextImportRequest(BaseModel):
    """Import deterministic ranged observations from human-authored text."""

    version: str = Field(pattern="^timeline_notes_text_v1$")
    content: str = Field(min_length=1)
    provenance: str = Field(default="manual_timeline_import", min_length=1, max_length=100)


class TimelineTextInvalidLine(BaseModel):
    line_number: int
    line: str
    reason: str


class TimelineNotesTextImportResult(BaseModel):
    parsed_count: int
    imported_count: int
    skipped_count: int
    invalid_lines: list[TimelineTextInvalidLine]
    ignored_line_count: int


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
    media_kind: str
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
    notes_filename: str | None
    raw_notes: str | None
    media: MediaOut
    notes: list[NoteOut]


class JobOut(ORMModel):
    id: UUID
    temporal_index_id: UUID | None
    alignment_run_id: UUID | None
    project_id: UUID
    session_id: UUID | None
    media_id: UUID
    transcription_id: UUID | None
    image_semantic_index_id: UUID | None
    temporal_derivative_id: UUID | None
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
