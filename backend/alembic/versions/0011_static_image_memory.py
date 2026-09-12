"""Add first-class source images and semantic image indexes.

Revision ID: 0011_static_image_memory
Revises: 0010_strict_note_range
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0011_static_image_memory"
down_revision = "0010_strict_note_range"
branch_labels = None
depends_on = None

EMBEDDING_DIMENSIONS = 1024


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.add_column(
        "media_assets",
        sa.Column("media_kind", sa.String(30), nullable=False, server_default="video"),
    )
    op.create_check_constraint(
        "ck_media_assets_kind", "media_assets", "media_kind IN ('video', 'image')"
    )
    op.create_index(
        "uq_media_assets_project_image_sha256",
        "media_assets",
        ["project_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("media_kind = 'image'"),
    )

    op.create_table(
        "media_asset_provenance",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "project_id",
            uuid,
            sa.ForeignKey("media_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "media_id",
            uuid,
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_owner", sa.String(200)),
        sa.Column("source_class", sa.String(100)),
        sa.Column("asset_class", sa.String(100)),
        sa.Column("collection", sa.String(200)),
        sa.Column("game", sa.String(200)),
        sa.Column("declared_metadata", postgresql.JSONB, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("media_id", name="uq_media_asset_provenance_media"),
    )
    op.create_index(
        "ix_media_asset_provenance_project_owner",
        "media_asset_provenance",
        ["project_id", "source_owner"],
    )
    op.create_index(
        "ix_media_asset_provenance_project_class",
        "media_asset_provenance",
        ["project_id", "asset_class"],
    )
    op.create_index(
        "ix_media_asset_provenance_project_collection",
        "media_asset_provenance",
        ["project_id", "collection"],
    )
    op.create_index(
        "ix_media_asset_provenance_project_game",
        "media_asset_provenance",
        ["project_id", "game"],
    )
    op.execute(
        """
        CREATE FUNCTION immutable_image_provenance() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'media asset provenance is immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER media_asset_provenance_immutable
        BEFORE UPDATE ON media_asset_provenance
        FOR EACH ROW EXECUTE FUNCTION immutable_image_provenance()
        """
    )

    op.create_table(
        "image_semantic_indexes",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "project_id",
            uuid,
            sa.ForeignKey("media_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "media_id",
            uuid,
            sa.ForeignKey("media_assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("strategy", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("result_schema_version", sa.String(50), nullable=False),
        sa.Column("canonical_text_version", sa.String(50), nullable=False),
        sa.Column("vision_provider", sa.String(100), nullable=False),
        sa.Column("vision_model", sa.String(200), nullable=False),
        sa.Column("configuration", postgresql.JSONB, nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("semantic_result", postgresql.JSONB),
        sa.Column("canonical_search_text", sa.Text),
        sa.Column("embedding_provider", sa.String(100), nullable=False),
        sa.Column("embedding_model", sa.String(200), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer, nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS)),
        sa.Column("provider_metadata", postgresql.JSONB),
        sa.Column("usage_metadata", postgresql.JSONB),
        sa.Column("error", sa.Text),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "media_id", "configuration_fingerprint", name="uq_image_index_media_config"
        ),
        sa.CheckConstraint(
            "embedding_dimensions = 1024", name="ck_image_index_embedding_dimensions"
        ),
        sa.CheckConstraint(
            "status != 'completed' OR "
            "(semantic_result IS NOT NULL AND canonical_search_text IS NOT NULL "
            "AND embedding IS NOT NULL)",
            name="ck_image_index_completed_outputs",
        ),
    )
    op.create_index(
        "ix_image_indexes_project_status",
        "image_semantic_indexes",
        ["project_id", "status"],
    )
    op.execute(
        """
        CREATE INDEX ix_image_indexes_embedding_hnsw
        ON image_semantic_indexes USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL AND status = 'completed'
        """
    )

    op.alter_column("processing_jobs", "session_id", existing_type=uuid, nullable=True)
    op.add_column("processing_jobs", sa.Column("image_semantic_index_id", uuid))
    op.create_foreign_key(
        "fk_processing_jobs_image_semantic_index",
        "processing_jobs",
        "image_semantic_indexes",
        ["image_semantic_index_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_processing_jobs_image_semantic_index",
        "processing_jobs",
        ["image_semantic_index_id"],
    )
    op.create_check_constraint(
        "ck_processing_job_session_or_image",
        "processing_jobs",
        "session_id IS NOT NULL OR image_semantic_index_id IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_processing_job_image_without_session",
        "processing_jobs",
        "image_semantic_index_id IS NULL OR session_id IS NULL",
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE processing_jobs "
        "DROP CONSTRAINT IF EXISTS ck_processing_job_image_without_session"
    )
    op.execute(
        "ALTER TABLE processing_jobs "
        "DROP CONSTRAINT IF EXISTS ck_processing_job_session_or_image"
    )
    op.drop_index("ix_processing_jobs_image_semantic_index", table_name="processing_jobs")
    op.drop_constraint(
        "fk_processing_jobs_image_semantic_index", "processing_jobs", type_="foreignkey"
    )
    op.drop_column("processing_jobs", "image_semantic_index_id")
    op.execute("DELETE FROM processing_jobs WHERE session_id IS NULL")
    op.alter_column("processing_jobs", "session_id", nullable=False)
    op.drop_index("ix_image_indexes_embedding_hnsw", table_name="image_semantic_indexes")
    op.drop_index("ix_image_indexes_project_status", table_name="image_semantic_indexes")
    op.drop_table("image_semantic_indexes")
    op.drop_index("ix_media_asset_provenance_project_game", table_name="media_asset_provenance")
    op.drop_index(
        "ix_media_asset_provenance_project_collection", table_name="media_asset_provenance"
    )
    op.drop_index("ix_media_asset_provenance_project_class", table_name="media_asset_provenance")
    op.drop_index("ix_media_asset_provenance_project_owner", table_name="media_asset_provenance")
    op.execute("DROP FUNCTION IF EXISTS immutable_image_provenance() CASCADE")
    op.drop_table("media_asset_provenance")
    op.execute("DELETE FROM media_assets WHERE media_kind = 'image'")
    op.drop_index("uq_media_assets_project_image_sha256", table_name="media_assets")
    op.drop_constraint("ck_media_assets_kind", "media_assets", type_="check")
    op.drop_column("media_assets", "media_kind")
