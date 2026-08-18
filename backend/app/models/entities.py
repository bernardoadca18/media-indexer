from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
    notes_filename: Mapped[str] = mapped_column(String(500))
    raw_notes: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    project: Mapped[MediaProject] = relationship(back_populates="sessions")
    media: Mapped[MediaAsset] = relationship()
    notes: Mapped[list["Note"]] = relationship(order_by="Note.order", cascade="all, delete-orphan")


class Note(Base, UUIDPrimaryKey):
    __tablename__ = "notes"
    __table_args__ = (UniqueConstraint("session_id", "order", name="uq_note_session_order"),)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("media_sessions.id", ondelete="CASCADE"))
    order: Mapped[int] = mapped_column(Integer)
    raw: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessingJob(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "processing_jobs"
    __table_args__ = (Index("ix_processing_jobs_status_created", "status", "created_at"),)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("media_projects.id", ondelete="CASCADE"))
    session_id: Mapped[UUID] = mapped_column(ForeignKey("media_sessions.id", ondelete="CASCADE"))
    media_id: Mapped[UUID] = mapped_column(ForeignKey("media_assets.id", ondelete="CASCADE"))
    job_type: Mapped[str] = mapped_column(String(50), default="initial_index")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    stage: Mapped[str] = mapped_column(String(100), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
