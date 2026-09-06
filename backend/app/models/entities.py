from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKey


class MediaProject(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "media_projects"
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    sessions: Mapped[list["MediaSession"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class MediaAsset(Base, UUIDPrimaryKey):
    __tablename__ = "media_assets"
    __table_args__ = (Index("ix_media_assets_project_sha256", "project_id", "sha256"),)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("media_projects.id", ondelete="CASCADE"))
    storage_uri: Mapped[str] = mapped_column(String(1000), unique=True)
    original_filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(200))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    format_name: Mapped[str | None] = mapped_column(String(200))
    video_codec: Mapped[str | None] = mapped_column(String(100))
    audio_codec: Mapped[str | None] = mapped_column(String(100))
    fps: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    probe_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MediaSession(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "media_sessions"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_session_project_idempotency"),
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("media_projects.id", ondelete="CASCADE"))
    media_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), unique=True
    )
    label: Mapped[str] = mapped_column(String(200))
    notes_filename: Mapped[str | None] = mapped_column(String(500))
    raw_notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    project: Mapped[MediaProject] = relationship(back_populates="sessions")
    media: Mapped[MediaAsset] = relationship()
    notes: Mapped[list["Note"]] = relationship(order_by="Note.order", cascade="all, delete-orphan")


class Note(Base, UUIDPrimaryKey):
    __tablename__ = "notes"
    __table_args__ = (
        UniqueConstraint("session_id", "order", name="uq_note_session_order"),
        CheckConstraint(
            "start_seconds IS NULL OR start_seconds >= 0", name="ck_note_start_nonnegative"
        ),
        CheckConstraint(
            "start_seconds IS NULL OR end_seconds IS NULL OR end_seconds >= start_seconds",
            name="ck_note_valid_range",
        ),
        Index("ix_notes_session_start", "session_id", "start_seconds"),
    )
    session_id: Mapped[UUID] = mapped_column(ForeignKey("media_sessions.id", ondelete="CASCADE"))
    order: Mapped[int] = mapped_column(Integer)
    raw: Mapped[str] = mapped_column(Text)
    # Derived temporal fields for structured timeline notes
    start_seconds: Mapped[float | None] = mapped_column(Float)
    end_seconds: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(JSON)
    # Provenance tracking for timeline notes
    provenance: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Transcription(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "transcriptions"
    __table_args__ = (
        UniqueConstraint(
            "media_id", "configuration_fingerprint", name="uq_transcription_media_config"
        ),
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("media_projects.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("media_sessions.id", ondelete="CASCADE"))
    media_id: Mapped[UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    language_requested: Mapped[str | None] = mapped_column(String(30))
    language_detected: Mapped[str | None] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    provider_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    segments: Mapped[list["TranscriptSegment"]] = relationship(
        order_by="TranscriptSegment.sequence", cascade="all, delete-orphan"
    )


class TranscriptSegment(Base, UUIDPrimaryKey):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        UniqueConstraint("transcription_id", "sequence", name="uq_segment_transcription_sequence"),
        Index("ix_transcript_segments_transcription_start", "transcription_id", "start_seconds"),
    )
    transcription_id: Mapped[UUID] = mapped_column(
        ForeignKey("transcriptions.id", ondelete="CASCADE")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    avg_logprob: Mapped[float | None] = mapped_column(Float)
    no_speech_prob: Mapped[float | None] = mapped_column(Float)
    compression_ratio: Mapped[float | None] = mapped_column(Float)
    word_timestamps: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VisualIndex(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "visual_indexes"
    __table_args__ = (
        UniqueConstraint(
            "media_id", "configuration_fingerprint", name="uq_visual_index_media_config"
        ),
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("media_projects.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("media_sessions.id", ondelete="CASCADE"))
    media_id: Mapped[UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    detector: Mapped[str] = mapped_column(String(100))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    tool_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scenes: Mapped[list["SceneSegment"]] = relationship(
        order_by="SceneSegment.sequence", cascade="all, delete-orphan"
    )
    derived_assets: Mapped[list["DerivedAsset"]] = relationship(
        order_by="DerivedAsset.timestamp_seconds", cascade="all, delete-orphan"
    )


class SceneSegment(Base, UUIDPrimaryKey):
    __tablename__ = "scene_segments"
    __table_args__ = (
        UniqueConstraint("visual_index_id", "sequence", name="uq_scene_visual_index_sequence"),
        CheckConstraint("start_seconds >= 0", name="ck_scene_start_nonnegative"),
        CheckConstraint("end_seconds >= start_seconds", name="ck_scene_valid_range"),
        CheckConstraint("duration_seconds >= 0", name="ck_scene_duration_nonnegative"),
        Index("ix_scene_segments_visual_start", "visual_index_id", "start_seconds"),
    )
    visual_index_id: Mapped[UUID] = mapped_column(
        ForeignKey("visual_indexes.id", ondelete="CASCADE")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float)
    detector_score: Mapped[float | None] = mapped_column(Float)
    segment_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DerivedAsset(Base, UUIDPrimaryKey):
    __tablename__ = "derived_assets"
    __table_args__ = (
        CheckConstraint(
            "timestamp_seconds IS NULL OR timestamp_seconds >= 0",
            name="ck_derived_timestamp_nonnegative",
        ),
        CheckConstraint(
            "source_start_seconds IS NULL OR source_start_seconds >= 0",
            name="ck_derived_source_start_nonnegative",
        ),
        CheckConstraint(
            "source_end_seconds IS NULL OR source_start_seconds IS NULL "
            "OR source_end_seconds >= source_start_seconds",
            name="ck_derived_source_valid_range",
        ),
        Index("ix_derived_assets_visual_timestamp", "visual_index_id", "timestamp_seconds"),
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("media_projects.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("media_sessions.id", ondelete="CASCADE"))
    media_id: Mapped[UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"))
    visual_index_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("visual_indexes.id", ondelete="CASCADE")
    )
    alignment_run_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("alignment_runs.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(100))
    storage_uri: Mapped[str] = mapped_column(String(1000), unique=True)
    mime_type: Mapped[str] = mapped_column(String(200))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    timestamp_seconds: Mapped[float | None] = mapped_column(Float)
    source_start_seconds: Mapped[float | None] = mapped_column(Float)
    source_end_seconds: Mapped[float | None] = mapped_column(Float)
    asset_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


EMBEDDING_VECTOR_DIMENSIONS = 1024


class TemporalIndex(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "temporal_indexes"
    __table_args__ = (
        UniqueConstraint(
            "media_id", "configuration_fingerprint", name="uq_temporal_index_media_config"
        ),
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("media_projects.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("media_sessions.id", ondelete="CASCADE"))
    media_id: Mapped[UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"))
    transcription_id: Mapped[UUID] = mapped_column(
        ForeignKey("transcriptions.id", ondelete="CASCADE")
    )
    visual_index_id: Mapped[UUID] = mapped_column(
        ForeignKey("visual_indexes.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(30), default="pending")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    embedding_provider: Mapped[str] = mapped_column(String(100))
    embedding_model: Mapped[str] = mapped_column(String(200))
    embedding_dimensions: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    segments: Mapped[list["TemporalSegment"]] = relationship(
        order_by="TemporalSegment.sequence", cascade="all, delete-orphan"
    )


class TemporalSegment(Base, UUIDPrimaryKey):
    __tablename__ = "temporal_segments"
    __table_args__ = (
        UniqueConstraint(
            "temporal_index_id", "sequence", name="uq_temporal_segment_index_sequence"
        ),
        CheckConstraint("start_seconds >= 0", name="ck_temporal_segment_start_nonnegative"),
        CheckConstraint("end_seconds > start_seconds", name="ck_temporal_segment_valid_range"),
        CheckConstraint("duration_seconds > 0", name="ck_temporal_segment_duration_positive"),
        Index("ix_temporal_segments_index_start", "temporal_index_id", "start_seconds"),
    )
    temporal_index_id: Mapped[UUID] = mapped_column(
        ForeignKey("temporal_indexes.id", ondelete="CASCADE")
    )
    sequence: Mapped[int] = mapped_column(Integer)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    segment_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_VECTOR_DIMENSIONS).with_variant(JSON(), "sqlite")
    )
    embedding_status: Mapped[str] = mapped_column(String(30), default="pending")
    embedding_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    transcript_links: Mapped[list["TemporalSegmentTranscriptLink"]] = relationship(
        cascade="all, delete-orphan"
    )
    scene_links: Mapped[list["TemporalSegmentSceneLink"]] = relationship(
        cascade="all, delete-orphan"
    )
    frame_links: Mapped[list["TemporalSegmentFrameLink"]] = relationship(
        cascade="all, delete-orphan"
    )


class TemporalSegmentTranscriptLink(Base):
    __tablename__ = "temporal_segment_transcripts"
    temporal_segment_id: Mapped[UUID] = mapped_column(
        ForeignKey("temporal_segments.id", ondelete="CASCADE"), primary_key=True
    )
    transcript_segment_id: Mapped[UUID] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), primary_key=True
    )


class TemporalSegmentSceneLink(Base):
    __tablename__ = "temporal_segment_scenes"
    temporal_segment_id: Mapped[UUID] = mapped_column(
        ForeignKey("temporal_segments.id", ondelete="CASCADE"), primary_key=True
    )
    scene_segment_id: Mapped[UUID] = mapped_column(
        ForeignKey("scene_segments.id", ondelete="CASCADE"), primary_key=True
    )


class TemporalSegmentFrameLink(Base):
    __tablename__ = "temporal_segment_frames"
    temporal_segment_id: Mapped[UUID] = mapped_column(
        ForeignKey("temporal_segments.id", ondelete="CASCADE"), primary_key=True
    )
    derived_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("derived_assets.id", ondelete="CASCADE"), primary_key=True
    )


class RetrievalRun(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "retrieval_runs"
    __table_args__ = (
        UniqueConstraint(
            "temporal_index_id",
            "configuration_fingerprint",
            name="uq_retrieval_run_temporal_config",
        ),
        Index("ix_retrieval_runs_session_created", "session_id", "created_at"),
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("media_projects.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("media_sessions.id", ondelete="CASCADE"))
    media_id: Mapped[UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"))
    temporal_index_id: Mapped[UUID] = mapped_column(
        ForeignKey("temporal_indexes.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(30))
    strategy: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    query_text: Mapped[str | None] = mapped_column(Text)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    candidates: Mapped[list["RetrievalCandidate"]] = relationship(
        order_by="RetrievalCandidate.rank", cascade="all, delete-orphan"
    )


class RetrievalCandidate(Base, UUIDPrimaryKey):
    __tablename__ = "retrieval_candidates"
    __table_args__ = (
        UniqueConstraint(
            "retrieval_run_id", "subject_key", "rank", name="uq_candidate_subject_rank"
        ),
        CheckConstraint("rank > 0", name="ck_retrieval_candidate_rank_positive"),
        CheckConstraint("start_seconds >= 0", name="ck_retrieval_candidate_start_nonnegative"),
        CheckConstraint("end_seconds > start_seconds", name="ck_retrieval_candidate_valid_range"),
        CheckConstraint("duration_seconds > 0", name="ck_retrieval_candidate_duration_positive"),
        Index("ix_retrieval_candidates_run_rank", "retrieval_run_id", "rank"),
        Index("ix_retrieval_candidates_note", "note_id"),
    )
    retrieval_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("retrieval_runs.id", ondelete="CASCADE")
    )
    note_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("notes.id", ondelete="CASCADE"))
    subject_key: Mapped[str] = mapped_column(String(100))
    rank: Mapped[int] = mapped_column(Integer)
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)
    duration_seconds: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float)
    score_components: Mapped[dict[str, Any]] = mapped_column(JSON)
    candidate_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    temporal_segment_links: Mapped[list["RetrievalCandidateTemporalSegment"]] = relationship(
        cascade="all, delete-orphan"
    )


class RetrievalCandidateTemporalSegment(Base):
    __tablename__ = "retrieval_candidate_temporal_segments"
    __table_args__ = (
        CheckConstraint("hit_rank > 0", name="ck_retrieval_candidate_hit_rank_positive"),
        Index("ix_retrieval_candidate_segments_temporal", "temporal_segment_id"),
    )
    retrieval_candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("retrieval_candidates.id", ondelete="CASCADE"), primary_key=True
    )
    temporal_segment_id: Mapped[UUID] = mapped_column(
        ForeignKey("temporal_segments.id", ondelete="CASCADE"), primary_key=True
    )
    similarity_score: Mapped[float] = mapped_column(Float)
    hit_rank: Mapped[int] = mapped_column(Integer)


class AlignmentRun(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "alignment_runs"
    __table_args__ = (
        UniqueConstraint(
            "retrieval_run_id",
            "configuration_fingerprint",
            name="uq_alignment_run_retrieval_config",
        ),
        Index("ix_alignment_runs_session_created", "session_id", "created_at"),
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey("media_projects.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("media_sessions.id", ondelete="CASCADE"))
    media_id: Mapped[UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"))
    retrieval_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("retrieval_runs.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    strategy: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    provider_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    usage_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subjects: Mapped[list["AlignmentSubject"]] = relationship(
        order_by="AlignmentSubject.subject_key", cascade="all, delete-orphan"
    )


class AlignmentSubject(Base, UUIDPrimaryKey):
    __tablename__ = "alignment_subjects"
    __table_args__ = (
        UniqueConstraint(
            "alignment_run_id",
            "subject_key",
            name="uq_alignment_subject_run_key",
        ),
        Index("ix_alignment_subjects_run_status", "alignment_run_id", "status"),
    )
    alignment_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("alignment_runs.id", ondelete="CASCADE")
    )
    subject_key: Mapped[str] = mapped_column(String(100))
    note_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("notes.id", ondelete="CASCADE"))
    observation: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    selected_candidate_alignment_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "candidate_alignments.id",
            name="fk_alignment_subject_selected_candidate",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    results: Mapped[list["CandidateAlignment"]] = relationship(
        foreign_keys="CandidateAlignment.alignment_subject_id",
        order_by="CandidateAlignment.retrieval_rank",
        cascade="all, delete-orphan",
    )


class CandidateAlignment(Base, UUIDPrimaryKey):
    __tablename__ = "candidate_alignments"
    __table_args__ = (
        UniqueConstraint(
            "alignment_run_id",
            "retrieval_candidate_id",
            name="uq_candidate_alignment_run_candidate",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_candidate_alignment_confidence",
        ),
        CheckConstraint(
            "status != 'matched' OR "
            "(start_seconds IS NOT NULL AND end_seconds IS NOT NULL "
            "AND start_seconds >= 0 AND end_seconds > start_seconds "
            "AND confidence IS NOT NULL)",
            name="ck_candidate_alignment_matched_range",
        ),
        CheckConstraint(
            "status != 'unmatched' OR "
            "(start_seconds IS NULL AND end_seconds IS NULL "
            "AND confidence IS NOT NULL)",
            name="ck_candidate_alignment_unmatched_range",
        ),
        CheckConstraint(
            "timestamp_resolution_seconds IS NULL " "OR timestamp_resolution_seconds > 0",
            name="ck_candidate_alignment_resolution_positive",
        ),
        Index(
            "ix_candidate_alignments_subject_rank",
            "alignment_subject_id",
            "retrieval_rank",
        ),
        Index("ix_candidate_alignments_candidate", "retrieval_candidate_id"),
    )
    alignment_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("alignment_runs.id", ondelete="CASCADE")
    )
    alignment_subject_id: Mapped[UUID] = mapped_column(
        ForeignKey("alignment_subjects.id", ondelete="CASCADE")
    )
    retrieval_candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("retrieval_candidates.id", ondelete="CASCADE")
    )
    note_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("notes.id", ondelete="CASCADE"))
    retrieval_rank: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    start_seconds: Mapped[float | None] = mapped_column(Float)
    end_seconds: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    anchors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    reasoning_summary: Mapped[str | None] = mapped_column(Text)
    timestamp_resolution_seconds: Mapped[float | None] = mapped_column(Float)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)
    provider_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    usage_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    latency_seconds: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProcessingJob(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "processing_jobs"
    __table_args__ = (Index("ix_processing_jobs_status_created", "status", "created_at"),)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("media_projects.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("media_sessions.id", ondelete="CASCADE"))
    media_id: Mapped[UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"))
    transcription_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("transcriptions.id", ondelete="CASCADE")
    )
    visual_index_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("visual_indexes.id", ondelete="CASCADE")
    )
    temporal_index_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("temporal_indexes.id", ondelete="CASCADE")
    )
    alignment_run_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("alignment_runs.id", ondelete="CASCADE")
    )
    job_type: Mapped[str] = mapped_column(String(50), default="initial_index")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    stage: Mapped[str] = mapped_column(String(100), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
