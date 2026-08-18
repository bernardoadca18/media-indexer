"""Add deterministic visual indexes and managed derived assets."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_visual_indexes"
down_revision = "0002_transcriptions"
branch_labels = None
depends_on = None
IMMUTABLE_RAW_FUNCTION = """
CREATE OR REPLACE FUNCTION immutable_raw() RETURNS trigger AS $$
BEGIN
  IF TG_TABLE_NAME = 'notes'
     AND to_jsonb(NEW)->'raw' IS DISTINCT FROM to_jsonb(OLD)->'raw'
  THEN
    RAISE EXCEPTION 'raw is immutable';
  END IF;
  IF TG_TABLE_NAME = 'media_sessions'
     AND to_jsonb(NEW)->'raw_notes' IS DISTINCT FROM to_jsonb(OLD)->'raw_notes'
  THEN
    RAISE EXCEPTION 'raw_notes is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

LEGACY_IMMUTABLE_RAW_FUNCTION = """
CREATE OR REPLACE FUNCTION immutable_raw() RETURNS trigger AS $$
BEGIN
  IF TG_TABLE_NAME = 'notes' AND NEW.raw IS DISTINCT FROM OLD.raw
  THEN RAISE EXCEPTION 'raw is immutable'; END IF;
  IF TG_TABLE_NAME = 'media_sessions'
     AND NEW.raw_notes IS DISTINCT FROM OLD.raw_notes
  THEN RAISE EXCEPTION 'raw_notes is immutable'; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.execute(IMMUTABLE_RAW_FUNCTION)
    op.create_table(
        "visual_indexes",
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
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("detector", sa.String(100), nullable=False),
        sa.Column("configuration", postgresql.JSONB, nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("tool_metadata", postgresql.JSONB),
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
            name="uq_visual_index_media_config",
        ),
    )
    op.create_table(
        "scene_segments",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "visual_index_id",
            uuid,
            sa.ForeignKey("visual_indexes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("start_seconds", sa.Float, nullable=False),
        sa.Column("end_seconds", sa.Float, nullable=False),
        sa.Column("duration_seconds", sa.Float, nullable=False),
        sa.Column("detector_score", sa.Float),
        sa.Column("metadata", postgresql.JSONB),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("visual_index_id", "sequence", name="uq_scene_visual_index_sequence"),
        sa.CheckConstraint("start_seconds >= 0", name="ck_scene_start_nonnegative"),
        sa.CheckConstraint("end_seconds >= start_seconds", name="ck_scene_valid_range"),
        sa.CheckConstraint("duration_seconds >= 0", name="ck_scene_duration_nonnegative"),
    )
    op.create_index(
        "ix_scene_segments_visual_start",
        "scene_segments",
        ["visual_index_id", "start_seconds"],
    )
    op.create_table(
        "derived_assets",
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
            "visual_index_id",
            uuid,
            sa.ForeignKey("visual_indexes.id", ondelete="CASCADE"),
        ),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("storage_uri", sa.String(1000), nullable=False, unique=True),
        sa.Column("mime_type", sa.String(200), nullable=False),
        sa.Column("byte_size", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("timestamp_seconds", sa.Float),
        sa.Column("source_start_seconds", sa.Float),
        sa.Column("source_end_seconds", sa.Float),
        sa.Column("metadata", postgresql.JSONB),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "timestamp_seconds IS NULL OR timestamp_seconds >= 0",
            name="ck_derived_timestamp_nonnegative",
        ),
        sa.CheckConstraint(
            "source_start_seconds IS NULL OR source_start_seconds >= 0",
            name="ck_derived_source_start_nonnegative",
        ),
        sa.CheckConstraint(
            "source_end_seconds IS NULL OR source_start_seconds IS NULL "
            "OR source_end_seconds >= source_start_seconds",
            name="ck_derived_source_valid_range",
        ),
    )
    op.create_index(
        "ix_derived_assets_visual_timestamp",
        "derived_assets",
        ["visual_index_id", "timestamp_seconds"],
    )
    op.add_column("processing_jobs", sa.Column("visual_index_id", uuid))
    op.create_foreign_key(
        "fk_processing_jobs_visual_index",
        "processing_jobs",
        "visual_indexes",
        ["visual_index_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_processing_jobs_visual_index",
        "processing_jobs",
        type_="foreignkey",
    )
    op.drop_column("processing_jobs", "visual_index_id")
    op.drop_index("ix_derived_assets_visual_timestamp", table_name="derived_assets")
    op.drop_table("derived_assets")
    op.drop_index("ix_scene_segments_visual_start", table_name="scene_segments")
    op.drop_table("scene_segments")
    op.drop_table("visual_indexes")
    op.execute(LEGACY_IMMUTABLE_RAW_FUNCTION)
