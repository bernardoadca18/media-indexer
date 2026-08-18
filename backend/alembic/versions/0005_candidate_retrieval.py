"""Add persisted candidate retrieval runs and evidence links."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005_candidate_retrieval"
down_revision = "0004_temporal_indexes_embeddings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "retrieval_runs",
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
            "temporal_index_id",
            uuid,
            sa.ForeignKey("temporal_indexes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("strategy", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("query_text", sa.Text),
        sa.Column("configuration", postgresql.JSONB, nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
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
            "temporal_index_id",
            "configuration_fingerprint",
            name="uq_retrieval_run_temporal_config",
        ),
    )
    op.create_index(
        "ix_retrieval_runs_session_created",
        "retrieval_runs",
        ["session_id", "created_at"],
    )
    op.create_table(
        "retrieval_candidates",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "retrieval_run_id",
            uuid,
            sa.ForeignKey("retrieval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "note_id",
            uuid,
            sa.ForeignKey("notes.id", ondelete="CASCADE"),
        ),
        sa.Column("subject_key", sa.String(100), nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("start_seconds", sa.Float, nullable=False),
        sa.Column("end_seconds", sa.Float, nullable=False),
        sa.Column("duration_seconds", sa.Float, nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("score_components", postgresql.JSONB, nullable=False),
        sa.Column("metadata", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "retrieval_run_id",
            "subject_key",
            "rank",
            name="uq_candidate_subject_rank",
        ),
        sa.CheckConstraint(
            "rank > 0",
            name="ck_retrieval_candidate_rank_positive",
        ),
        sa.CheckConstraint(
            "start_seconds >= 0",
            name="ck_retrieval_candidate_start_nonnegative",
        ),
        sa.CheckConstraint(
            "end_seconds > start_seconds",
            name="ck_retrieval_candidate_valid_range",
        ),
        sa.CheckConstraint(
            "duration_seconds > 0",
            name="ck_retrieval_candidate_duration_positive",
        ),
    )
    op.create_index(
        "ix_retrieval_candidates_run_rank",
        "retrieval_candidates",
        ["retrieval_run_id", "rank"],
    )
    op.create_index(
        "ix_retrieval_candidates_note",
        "retrieval_candidates",
        ["note_id"],
    )
    op.create_table(
        "retrieval_candidate_temporal_segments",
        sa.Column(
            "retrieval_candidate_id",
            uuid,
            sa.ForeignKey("retrieval_candidates.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "temporal_segment_id",
            uuid,
            sa.ForeignKey("temporal_segments.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("similarity_score", sa.Float, nullable=False),
        sa.Column("hit_rank", sa.Integer, nullable=False),
        sa.CheckConstraint(
            "hit_rank > 0",
            name="ck_retrieval_candidate_hit_rank_positive",
        ),
    )
    op.create_index(
        "ix_retrieval_candidate_segments_temporal",
        "retrieval_candidate_temporal_segments",
        ["temporal_segment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retrieval_candidate_segments_temporal",
        table_name="retrieval_candidate_temporal_segments",
    )
    op.drop_table("retrieval_candidate_temporal_segments")
    op.drop_index(
        "ix_retrieval_candidates_note",
        table_name="retrieval_candidates",
    )
    op.drop_index(
        "ix_retrieval_candidates_run_rank",
        table_name="retrieval_candidates",
    )
    op.drop_table("retrieval_candidates")
    op.drop_index(
        "ix_retrieval_runs_session_created",
        table_name="retrieval_runs",
    )
    op.drop_table("retrieval_runs")
