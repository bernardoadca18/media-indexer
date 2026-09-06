"""Add provenance field to notes for tracking import source.

Revision ID: 0009_note_provenance
Revises: 0008_structured_timeline_notes
Create Date: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0009_note_provenance"
down_revision = "0008_structured_timeline_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "notes",
        sa.Column("provenance", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notes", "provenance")
