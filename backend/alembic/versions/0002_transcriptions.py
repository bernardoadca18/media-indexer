"""Add durable job recovery and timestamped transcriptions."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_transcriptions"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "transcriptions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "project_id",
            uuid,
            sa.ForeignKey("media_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            uuid,
            sa.ForeignKey("media_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "media_id", uuid, sa.ForeignKey("media_assets.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("language_requested", sa.String(30)),
        sa.Column("language_detected", sa.String(30)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("configuration", postgresql.JSONB, nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("duration_seconds", sa.Float),
        sa.Column("provider_metadata", postgresql.JSONB),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "media_id", "configuration_fingerprint", name="uq_transcription_media_config"
        ),
    )
    op.create_table(
        "transcript_segments",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "transcription_id",
            uuid,
            sa.ForeignKey("transcriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("start_seconds", sa.Float, nullable=False),
        sa.Column("end_seconds", sa.Float, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("avg_logprob", sa.Float),
        sa.Column("no_speech_prob", sa.Float),
        sa.Column("compression_ratio", sa.Float),
        sa.Column("word_timestamps", postgresql.JSONB),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "transcription_id", "sequence", name="uq_segment_transcription_sequence"
        ),
    )
    op.create_index(
        "ix_transcript_segments_transcription_start",
        "transcript_segments",
        ["transcription_id", "start_seconds"],
    )
    op.add_column("processing_jobs", sa.Column("transcription_id", uuid))
    op.create_foreign_key(
        "fk_processing_jobs_transcription",
        "processing_jobs",
        "transcriptions",
        ["transcription_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column("processing_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True)))
    op.add_column(
        "processing_jobs",
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "processing_jobs", sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3")
    )
    op.create_index(
        "ix_processing_jobs_recovery",
        "processing_jobs",
        ["status", "heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_recovery", table_name="processing_jobs")
    op.drop_column("processing_jobs", "max_attempts")
    op.drop_column("processing_jobs", "attempt_count")
    op.drop_column("processing_jobs", "heartbeat_at")
    op.drop_constraint("fk_processing_jobs_transcription", "processing_jobs", type_="foreignkey")
    op.drop_column("processing_jobs", "transcription_id")
    op.drop_index("ix_transcript_segments_transcription_start", table_name="transcript_segments")
    op.drop_table("transcript_segments")
    op.drop_table("transcriptions")
