from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.domain import JobOut


class TemporalDerivativeProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: Literal["render_proxy_1080p30_v2"]
    strategy_version: Literal["proxy_strategy_v2"]
    container: Literal["mp4"]
    codec: Literal["h264"]
    encoder_backend: Literal["libx264"]
    max_width: int = Field(ge=16, le=7680)
    max_height: int = Field(ge=16, le=4320)
    scaling_policy: Literal["fit_inside_even_dimensions"]
    never_upscale: Literal[True]
    fps: int = Field(ge=1, le=120)
    pixel_format: Literal["yuv420p"]
    quality_mode: Literal["crf"]
    crf: int = Field(ge=0, le=51)
    preset: Literal["fast"]
    gop_frames: int = Field(ge=1, le=600)
    keyframe_policy: Literal["fixed_closed_gop"]
    scenecut: Literal[False]
    color_policy: Literal["bt709_passthrough"]
    audio_policy: Literal["remove"]
    subtitle_policy: Literal["remove"]
    data_policy: Literal["remove"]
    strip_metadata: Literal[True]
    movflags: Literal["faststart"]


class TemporalDerivativeEnsureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["temporal_derivative_request_v1"]
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_start_microseconds: int = Field(ge=0)
    source_end_microseconds: int = Field(gt=0)
    profile_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    profile: TemporalDerivativeProfile


class TemporalDerivativeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    identity: str
    source_media_id: UUID
    source_sha256: str
    source_start_microseconds: int
    source_end_microseconds: int
    profile_id: str
    profile_identity: str
    strategy_version: str
    status: str
    sha256: str | None
    byte_size: int | None
    duration_seconds: float | None
    width: int | None
    height: int | None
    fps: float | None
    codec: str | None
    generation_seconds: float | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    content_url: str | None = None


class TemporalDerivativeEnsureOut(BaseModel):
    derivative: TemporalDerivativeOut
    job: JobOut
    created: bool
