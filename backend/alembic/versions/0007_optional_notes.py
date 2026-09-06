"""Make notes optional in MediaSession.

Revision ID: 0007_optional_notes
Revises: 0006_multimodal_alignment
Create Date: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0007_optional_notes"
down_revision = "0006_multimodal_alignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make notes fields nullable for video-only ingest
    op.alter_column("media_sessions", "notes_filename", nullable=True)
    op.alter_column("media_sessions", "raw_notes", nullable=True)


def downgrade() -> None:
    # Revert to non-nullable (will fail if NULL values exist)
    op.alter_column("media_sessions", "raw_notes", nullable=False)
    op.alter_column("media_sessions", "notes_filename", nullable=False)
