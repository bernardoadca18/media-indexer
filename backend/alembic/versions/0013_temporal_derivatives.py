"""Add immutable temporal video derivatives and durable generation jobs.

Revision ID: 0013_temporal_derivatives
Revises: 0012_provenance_indexes
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013_temporal_derivatives"
down_revision = "0012_provenance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "temporal_derivatives",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "project_id",
            uuid,
            sa.ForeignKey("media_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_media_id",
            uuid,
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("identity", sa.String(64), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("source_start_microseconds", sa.BigInteger, nullable=False),
        sa.Column("source_end_microseconds", sa.BigInteger, nullable=False),
        sa.Column("profile_id", sa.String(100), nullable=False),
        sa.Column("profile_identity", sa.String(64), nullable=False),
        sa.Column("strategy_version", sa.String(100), nullable=False),
        sa.Column("specification", postgresql.JSONB, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("storage_uri", sa.String(1000), unique=True),
        sa.Column("sha256", sa.String(64)),
        sa.Column("byte_size", sa.BigInteger),
        sa.Column("duration_seconds", sa.Float),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("fps", sa.Float),
        sa.Column("codec", sa.String(100)),
        sa.Column("generation_seconds", sa.Float),
        sa.Column("error", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("identity", name="uq_temporal_derivatives_identity"),
        sa.CheckConstraint(
            "source_start_microseconds >= 0", name="ck_derivative_start_nonnegative"
        ),
        sa.CheckConstraint(
            "source_end_microseconds > source_start_microseconds",
            name="ck_derivative_valid_range",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_derivative_status",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR (storage_uri IS NOT NULL AND sha256 IS NOT NULL "
            "AND byte_size IS NOT NULL AND duration_seconds IS NOT NULL AND width IS NOT NULL "
            "AND height IS NOT NULL AND fps IS NOT NULL AND codec IS NOT NULL)",
            name="ck_derivative_completed_outputs",
        ),
    )
    op.create_index(
        "ix_temporal_derivatives_media_created",
        "temporal_derivatives",
        ["source_media_id", "created_at"],
    )
    op.create_index(
        "ix_temporal_derivatives_status_created",
        "temporal_derivatives",
        ["status", "created_at"],
    )
    op.drop_constraint("ck_processing_job_session_or_image", "processing_jobs", type_="check")
    op.add_column("processing_jobs", sa.Column("temporal_derivative_id", uuid))
    op.create_foreign_key(
        "fk_processing_jobs_temporal_derivative",
        "processing_jobs",
        "temporal_derivatives",
        ["temporal_derivative_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_processing_jobs_temporal_derivative",
        "processing_jobs",
        ["temporal_derivative_id"],
    )
    op.create_check_constraint(
        "ck_processing_job_owner",
        "processing_jobs",
        "session_id IS NOT NULL OR image_semantic_index_id IS NOT NULL "
        "OR temporal_derivative_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_processing_job_derivative_without_other_owner",
        "processing_jobs",
        "temporal_derivative_id IS NULL OR (session_id IS NULL "
        "AND image_semantic_index_id IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_processing_job_derivative_without_other_owner",
        "processing_jobs",
        type_="check",
    )
    op.drop_constraint("ck_processing_job_owner", "processing_jobs", type_="check")
    op.drop_index("ix_processing_jobs_temporal_derivative", table_name="processing_jobs")
    op.drop_constraint(
        "fk_processing_jobs_temporal_derivative", "processing_jobs", type_="foreignkey"
    )
    op.drop_column("processing_jobs", "temporal_derivative_id")
    op.create_check_constraint(
        "ck_processing_job_session_or_image",
        "processing_jobs",
        "session_id IS NOT NULL OR image_semantic_index_id IS NOT NULL",
    )
    op.drop_index("ix_temporal_derivatives_status_created", table_name="temporal_derivatives")
    op.drop_index("ix_temporal_derivatives_media_created", table_name="temporal_derivatives")
    op.drop_table("temporal_derivatives")
