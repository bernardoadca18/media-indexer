from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.domain import JobOut, ORMModel


class VisualIndexCreate(BaseModel):
    scene_detection_enabled: bool | None = None
    scene_detector: Literal["ffmpeg_scdet"] | None = None
    scene_threshold: float | None = Field(default=None, ge=0, le=100)
    periodic_frame_interval_seconds: float | None = Field(default=None, gt=0)
    frame_width: int | None = Field(default=None, ge=64, le=4096)
    image_format: Literal["jpeg"] | None = None
    jpeg_quality: int | None = Field(default=None, ge=1, le=100)
    dedup_tolerance_seconds: float | None = Field(default=None, ge=0, le=10)


class VisualIndexOut(ORMModel):
    id: UUID
    project_id: UUID
    session_id: UUID
    media_id: UUID
    status: str
    detector: str
    configuration: dict[str, Any]
    configuration_fingerprint: str
    tool_metadata: dict[str, Any] | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class VisualIndexJobOut(BaseModel):
    visual_index: VisualIndexOut
    job: JobOut
    created: bool


class SceneSegmentOut(ORMModel):
    id: UUID
    visual_index_id: UUID
    sequence: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    detector_score: float | None
    metadata: dict[str, Any] | None = Field(validation_alias="segment_metadata")
    created_at: datetime


class SceneSegmentPage(BaseModel):
    items: list[SceneSegmentOut]
    total: int
    offset: int
    limit: int


class DerivedAssetOut(ORMModel):
    id: UUID
    project_id: UUID
    session_id: UUID
    media_id: UUID
    visual_index_id: UUID | None
    kind: str
    storage_uri: str
    mime_type: str
    byte_size: int
    sha256: str
    width: int | None
    height: int | None
    timestamp_seconds: float | None
    source_start_seconds: float | None
    source_end_seconds: float | None
    metadata: dict[str, Any] | None = Field(validation_alias="asset_metadata")
    created_at: datetime


class DerivedAssetPage(BaseModel):
    items: list[DerivedAssetOut]
    total: int
    offset: int
    limit: int
