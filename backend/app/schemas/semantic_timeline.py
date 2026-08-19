from typing import Any
from uuid import UUID

from pydantic import BaseModel


class TimelineRangeOut(BaseModel):
    start_seconds: float | None
    end_seconds: float | None
    semantics: str = "[start,end)"


class TimelineTranscriptionProvenanceOut(BaseModel):
    transcription_id: UUID


class TimelineVisualProvenanceOut(BaseModel):
    visual_index_id: UUID


class TimelineAlignmentProvenanceOut(BaseModel):
    alignment_run_id: UUID
    retrieval_run_id: UUID
    provider: str
    model: str
    strategy: str
    prompt_version: str
    configuration_fingerprint: str


class TimelineProvenanceOut(BaseModel):
    project_id: UUID
    session_id: UUID
    media_id: UUID
    temporal_index_id: UUID
    temporal_index_fingerprint: str
    transcription: TimelineTranscriptionProvenanceOut
    visual_index: TimelineVisualProvenanceOut
    alignment: TimelineAlignmentProvenanceOut | None


class TimelineTranscriptRefOut(BaseModel):
    id: UUID
    sequence: int
    start_seconds: float
    end_seconds: float


class TimelineSceneRefOut(BaseModel):
    id: UUID
    sequence: int
    start_seconds: float
    end_seconds: float


class TimelineFrameRefOut(BaseModel):
    id: UUID
    kind: str
    timestamp_seconds: float
    storage_uri: str
    mime_type: str
    width: int | None
    height: int | None
    metadata: dict[str, Any] | None


class TimelineEvidenceOut(BaseModel):
    transcript_segments: list[TimelineTranscriptRefOut]
    scenes: list[TimelineSceneRefOut]
    frames: list[TimelineFrameRefOut]


class TimelineItemOut(BaseModel):
    temporal_segment_id: UUID
    sequence: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    text: str
    metadata: dict[str, Any] | None
    silent: bool
    evidence: TimelineEvidenceOut
    matched_note_ids: list[UUID]
    selected_alignment_ids: list[UUID]


class TimelineSelectedAlignmentOut(BaseModel):
    candidate_alignment_id: UUID
    retrieval_candidate_id: UUID
    retrieval_rank: int
    start_seconds: float
    end_seconds: float
    confidence: float
    anchors: list[dict[str, Any]]
    reasoning_summary: str | None
    timestamp_resolution_seconds: float | None
    temporal_segment_ids: list[UUID]


class TimelineNoteOut(BaseModel):
    id: UUID
    order: int
    raw: str
    alignment_status: str
    alignment_subject_id: UUID | None
    selected_alignment: TimelineSelectedAlignmentOut | None


class SemanticTimelineOut(BaseModel):
    provenance: TimelineProvenanceOut
    range: TimelineRangeOut
    items: list[TimelineItemOut]
    notes: list[TimelineNoteOut]
    total: int
    offset: int
    limit: int
