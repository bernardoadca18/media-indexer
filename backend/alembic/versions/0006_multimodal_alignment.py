"""Add provider-neutral multimodal temporal alignment."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006_multimodal_alignment"
down_revision = "0005_candidate_retrieval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "alignment_runs",
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
            "retrieval_run_id",
            uuid,
            sa.ForeignKey("retrieval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("strategy", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("configuration", postgresql.JSONB, nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("provider_metadata", postgresql.JSONB),
        sa.Column("usage_metadata", postgresql.JSONB),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "retrieval_run_id",
            "configuration_fingerprint",
            name="uq_alignment_run_retrieval_config",
        ),
    )
    op.create_index(
        "ix_alignment_runs_session_created",
        "alignment_runs",
        ["session_id", "created_at"],
    )
    op.create_table(
        "alignment_subjects",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "alignment_run_id",
            uuid,
            sa.ForeignKey("alignment_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_key", sa.String(100), nullable=False),
        sa.Column(
            "note_id",
            uuid,
            sa.ForeignKey("notes.id", ondelete="CASCADE"),
        ),
        sa.Column("observation", sa.Text, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("selected_candidate_alignment_id", uuid),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "alignment_run_id",
            "subject_key",
            name="uq_alignment_subject_run_key",
        ),
    )
    op.create_index(
        "ix_alignment_subjects_run_status",
        "alignment_subjects",
        ["alignment_run_id", "status"],
    )
    op.create_table(
        "candidate_alignments",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "alignment_run_id",
            uuid,
            sa.ForeignKey("alignment_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "alignment_subject_id",
            uuid,
            sa.ForeignKey("alignment_subjects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "retrieval_candidate_id",
            uuid,
            sa.ForeignKey("retrieval_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "note_id",
            uuid,
            sa.ForeignKey("notes.id", ondelete="CASCADE"),
        ),
        sa.Column("retrieval_rank", sa.Integer, nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("start_seconds", sa.Float),
        sa.Column("end_seconds", sa.Float),
        sa.Column("confidence", sa.Float),
        sa.Column("anchors", postgresql.JSONB, nullable=False),
        sa.Column("reasoning_summary", sa.Text),
        sa.Column("timestamp_resolution_seconds", sa.Float),
        sa.Column("pass_count", sa.Integer, nullable=False),
        sa.Column("provider_metadata", postgresql.JSONB),
        sa.Column("usage_metadata", postgresql.JSONB),
        sa.Column("latency_seconds", sa.Float),
        sa.Column("error", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "alignment_run_id",
            "retrieval_candidate_id",
            name="uq_candidate_alignment_run_candidate",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_candidate_alignment_confidence",
        ),
        sa.CheckConstraint(
            "status != 'matched' OR "
            "(start_seconds IS NOT NULL AND end_seconds IS NOT NULL "
            "AND start_seconds >= 0 AND end_seconds > start_seconds "
            "AND confidence IS NOT NULL)",
            name="ck_candidate_alignment_matched_range",
        ),
        sa.CheckConstraint(
            "status != 'unmatched' OR "
            "(start_seconds IS NULL AND end_seconds IS NULL "
            "AND confidence IS NOT NULL)",
            name="ck_candidate_alignment_unmatched_range",
        ),
        sa.CheckConstraint(
            "timestamp_resolution_seconds IS NULL " "OR timestamp_resolution_seconds > 0",
            name="ck_candidate_alignment_resolution_positive",
        ),
    )
    op.create_index(
        "ix_candidate_alignments_subject_rank",
        "candidate_alignments",
        ["alignment_subject_id", "retrieval_rank"],
    )
    op.create_index(
        "ix_candidate_alignments_candidate",
        "candidate_alignments",
        ["retrieval_candidate_id"],
    )
    op.create_foreign_key(
        "fk_alignment_subject_selected_candidate",
        "alignment_subjects",
        "candidate_alignments",
        ["selected_candidate_alignment_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("derived_assets", sa.Column("alignment_run_id", uuid))
    op.create_foreign_key(
        "fk_derived_assets_alignment_run",
        "derived_assets",
        "alignment_runs",
        ["alignment_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_derived_assets_alignment_timestamp",
        "derived_assets",
        ["alignment_run_id", "timestamp_seconds"],
    )
    op.add_column("processing_jobs", sa.Column("alignment_run_id", uuid))
    op.create_foreign_key(
        "fk_processing_jobs_alignment_run",
        "processing_jobs",
        "alignment_runs",
        ["alignment_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_processing_jobs_alignment_run",
        "processing_jobs",
        ["alignment_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_processing_jobs_alignment_run", table_name="processing_jobs")
    op.drop_constraint(
        "fk_processing_jobs_alignment_run",
        "processing_jobs",
        type_="foreignkey",
    )
    op.drop_column("processing_jobs", "alignment_run_id")
    op.drop_index(
        "ix_derived_assets_alignment_timestamp",
        table_name="derived_assets",
    )
    op.drop_constraint(
        "fk_derived_assets_alignment_run",
        "derived_assets",
        type_="foreignkey",
    )
    op.drop_column("derived_assets", "alignment_run_id")
    op.drop_constraint(
        "fk_alignment_subject_selected_candidate",
        "alignment_subjects",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_candidate_alignments_candidate",
        table_name="candidate_alignments",
    )
    op.drop_index(
        "ix_candidate_alignments_subject_rank",
        table_name="candidate_alignments",
    )
    op.drop_table("candidate_alignments")
    op.drop_index(
        "ix_alignment_subjects_run_status",
        table_name="alignment_subjects",
    )
    op.drop_table("alignment_subjects")
    op.drop_index(
        "ix_alignment_runs_session_created",
        table_name="alignment_runs",
    )
    op.drop_table("alignment_runs")
