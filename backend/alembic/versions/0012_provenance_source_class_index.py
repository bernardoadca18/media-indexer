"""Name the asset-class index precisely and index source class.

Revision ID: 0012_provenance_indexes
Revises: 0011_static_image_memory
"""

from alembic import op

revision = "0012_provenance_indexes"
down_revision = "0011_static_image_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER INDEX ix_media_asset_provenance_project_class "
        "RENAME TO ix_media_asset_provenance_project_asset_class"
    )
    op.create_index(
        "ix_media_asset_provenance_project_source_class",
        "media_asset_provenance",
        ["project_id", "source_class"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_media_asset_provenance_project_source_class",
        table_name="media_asset_provenance",
    )
    op.execute(
        "ALTER INDEX ix_media_asset_provenance_project_asset_class "
        "RENAME TO ix_media_asset_provenance_project_class"
    )
