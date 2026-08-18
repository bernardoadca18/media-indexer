from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.schemas.domain import JobOut, ORMModel


class TemporalIndexCreate(BaseModel):
    transcription_id: UUID
    visual_index_id: UUID
    target_duration_seconds: float | None = Field(default=None, gt=0)
    min_duration_seconds: float | None = Field(default=None, gt=0)
    max_duration_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_explicit_durations(self) -> "TemporalIndexCreate":
        values = (
            self.min_duration_seconds,
            self.target_duration_seconds,
            self.max_duration_seconds,
        )
        if all(value is not None for value in values):
            minimum, target, maximum = values
            assert minimum is not None and target is not None and maximum is not None
            if not minimum <= target <= maximum:
                raise ValueError("durations must satisfy min <= target <= max")
        return self


class TemporalIndexOut(ORMModel):
    id: UUID
    project_id: UUID
    session_id: UUID
    media_id: UUID
    transcription_id: UUID
    visual_index_id: UUID
    status: str
    configuration: dict[str, Any]
    configuration_fingerprint: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TemporalIndexJobOut(BaseModel):
    temporal_index: TemporalIndexOut
    job: JobOut
    created: bool


class TemporalSegmentOut(BaseModel):
    id: UUID
    temporal_index_id: UUID
    sequence: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    text: str
    search_text: str
    metadata: dict[str, Any] | None
    embedding_status: str
    embedding_created_at: datetime | None
    transcript_segment_ids: list[UUID]
    scene_segment_ids: list[UUID]
    frame_asset_ids: list[UUID]
    created_at: datetime


class TemporalSegmentPage(BaseModel):
    items: list[TemporalSegmentOut]
    total: int
    offset: int
    limit: int


class TemporalSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=100)


class TemporalSearchItem(BaseModel):
    temporal_segment_id: UUID
    start_seconds: float
    end_seconds: float
    score: float


class TemporalSearchOut(BaseModel):
    items: list[TemporalSearchItem]
    metric: str = "cosine"
