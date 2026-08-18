"""Add searchable temporal segments and pgvector embeddings."""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_temporal_indexes_embeddings"
down_revision = "0003_visual_indexes"
branch_labels = None
depends_on = None

EMBEDDING_DIMENSIONS = 1024


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "temporal_indexes",
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
            "media_id",
            uuid,
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "transcription_id",
            uuid,
            sa.ForeignKey("transcriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "visual_index_id",
            uuid,
            sa.ForeignKey("visual_indexes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("configuration", postgresql.JSONB, nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("embedding_provider", sa.String(100), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "media_id",
            "configuration_fingerprint",
            name="uq_temporal_index_media_config",
        ),
        sa.CheckConstraint(
            "embedding_dimensions = 1024",
            name="ck_temporal_index_embedding_dimensions",
        ),
    )
    op.create_table(
        "temporal_segments",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "temporal_index_id",
            uuid,
            sa.ForeignKey("temporal_indexes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("start_seconds", sa.Float, nullable=False),
        sa.Column("end_seconds", sa.Float, nullable=False),
        sa.Column("duration_seconds", sa.Float, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("search_text", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS)),
        sa.Column("embedding_status", sa.String(30), nullable=False),
        sa.Column("embedding_created_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "temporal_index_id",
            "sequence",
            name="uq_temporal_segment_index_sequence",
        ),
        sa.CheckConstraint(
            "start_seconds >= 0",
            name="ck_temporal_segment_start_nonnegative",
        ),
        sa.CheckConstraint(
            "end_seconds > start_seconds",
            name="ck_temporal_segment_valid_range",
        ),
        sa.CheckConstraint(
            "duration_seconds > 0",
            name="ck_temporal_segment_duration_positive",
        ),
    )
    op.create_index(
        "ix_temporal_segments_index_start",
        "temporal_segments",
        ["temporal_index_id", "start_seconds"],
    )
    op.create_table(
        "temporal_segment_transcripts",
        sa.Column(
            "temporal_segment_id",
            uuid,
            sa.ForeignKey("temporal_segments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "transcript_segment_id",
            uuid,
            sa.ForeignKey("transcript_segments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_temporal_segment_transcripts_evidence",
        "temporal_segment_transcripts",
        ["transcript_segment_id"],
    )
    op.create_table(
        "temporal_segment_scenes",
        sa.Column(
            "temporal_segment_id",
            uuid,
            sa.ForeignKey("temporal_segments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "scene_segment_id",
            uuid,
            sa.ForeignKey("scene_segments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_temporal_segment_scenes_evidence",
        "temporal_segment_scenes",
        ["scene_segment_id"],
    )
    op.create_table(
        "temporal_segment_frames",
        sa.Column(
            "temporal_segment_id",
            uuid,
            sa.ForeignKey("temporal_segments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "derived_asset_id",
            uuid,
            sa.ForeignKey("derived_assets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_temporal_segment_frames_evidence",
        "temporal_segment_frames",
        ["derived_asset_id"],
    )
    op.add_column("processing_jobs", sa.Column("temporal_index_id", uuid))
    op.create_foreign_key(
        "fk_processing_jobs_temporal_index",
        "processing_jobs",
        "temporal_indexes",
        ["temporal_index_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_processing_jobs_temporal_index",
        "processing_jobs",
        type_="foreignkey",
    )
    op.drop_column("processing_jobs", "temporal_index_id")
    op.drop_index(
        "ix_temporal_segment_frames_evidence",
        table_name="temporal_segment_frames",
    )
    op.drop_table("temporal_segment_frames")
    op.drop_index(
        "ix_temporal_segment_scenes_evidence",
        table_name="temporal_segment_scenes",
    )
    op.drop_table("temporal_segment_scenes")
    op.drop_index(
        "ix_temporal_segment_transcripts_evidence",
        table_name="temporal_segment_transcripts",
    )
    op.drop_table("temporal_segment_transcripts")
    op.drop_index("ix_temporal_segments_index_start", table_name="temporal_segments")
    op.drop_table("temporal_segments")
    op.drop_table("temporal_indexes")
    op.execute("DROP EXTENSION IF EXISTS vector")
