"""Add structured temporal fields to notes.

Revision ID: 0008_structured_timeline_notes
Revises: 0007_optional_notes
Create Date: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0008_structured_timeline_notes"
down_revision = "0007_optional_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add derived temporal fields for structured timeline notes
    # These are nullable - notes can be raw (no temporal data) or structured
    op.add_column("notes", sa.Column("start_seconds", sa.Float(), nullable=True))
    op.add_column("notes", sa.Column("end_seconds", sa.Float(), nullable=True))
    op.add_column("notes", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("notes", sa.Column("tags", sa.JSON(), nullable=True))

    # Add check constraints for valid temporal ranges
    op.create_check_constraint(
        "ck_note_start_nonnegative",
        "notes",
        "start_seconds IS NULL OR start_seconds >= 0",
    )
    op.create_check_constraint(
        "ck_note_valid_range",
        "notes",
        "start_seconds IS NULL OR end_seconds IS NULL OR end_seconds >= start_seconds",
    )

    # Add index for temporal queries
    op.create_index("ix_notes_session_start", "notes", ["session_id", "start_seconds"])


def downgrade() -> None:
    op.drop_index("ix_notes_session_start", table_name="notes")
    op.drop_constraint("ck_note_valid_range", "notes", type_="check")
    op.drop_constraint("ck_note_start_nonnegative", "notes", type_="check")
    op.drop_column("notes", "tags")
    op.drop_column("notes", "description")
    op.drop_column("notes", "end_seconds")
    op.drop_column("notes", "start_seconds")
