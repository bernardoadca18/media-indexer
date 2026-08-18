from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.domain import JobOut, ORMModel


class TranscriptionCreate(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=200)
    language: str | None = Field(default=None, max_length=30)
    beam_size: int | None = Field(default=None, ge=1, le=20)
    vad_filter: bool | None = None
    word_timestamps: bool | None = None


class TranscriptionOut(ORMModel):
    id: UUID
    project_id: UUID
    session_id: UUID
    media_id: UUID
    provider: str
    model: str
    language_requested: str | None
    language_detected: str | None
    status: str
    configuration: dict[str, Any]
    configuration_fingerprint: str
    duration_seconds: float | None
    provider_metadata: dict[str, Any] | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TranscriptionJobOut(BaseModel):
    transcription: TranscriptionOut
    job: JobOut
    created: bool


class TranscriptSegmentOut(ORMModel):
    id: UUID
    transcription_id: UUID
    sequence: int
    start_seconds: float
    end_seconds: float
    text: str
    avg_logprob: float | None
    no_speech_prob: float | None
    compression_ratio: float | None
    word_timestamps: list[dict[str, Any]] | None


class TranscriptSegmentPage(BaseModel):
    items: list[TranscriptSegmentOut]
    total: int
    offset: int
    limit: int
