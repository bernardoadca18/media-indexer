"""Enforce strict timeline range: end_seconds > start_seconds.

Revision ID: 0010_strict_note_range
Revises: 0009_note_provenance
Create Date: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0010_strict_note_range"
down_revision = "0009_note_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Check for existing records with end_seconds == start_seconds
    # These are invalid under the strict constraint and must be fixed manually
    conn = op.get_bind()
    result = conn.execute(
        text(
            """
            SELECT COUNT(*) as count
            FROM notes
            WHERE start_seconds IS NOT NULL
              AND end_seconds IS NOT NULL
              AND end_seconds = start_seconds
            """
        )
    )
    row = result.fetchone()
    invalid_count = row[0] if row else 0

    if invalid_count > 0:
        raise RuntimeError(
            f"Cannot apply migration: found {invalid_count} note(s) with "
            f"end_seconds == start_seconds. These represent zero-duration "
            f"intervals which are not allowed. Please fix or remove these "
            f"records before applying this migration. "
            f"Query to find them: SELECT * FROM notes WHERE start_seconds IS NOT NULL "
            f"AND end_seconds IS NOT NULL AND end_seconds = start_seconds;"
        )

    # Drop old constraint that allowed equality
    op.drop_constraint("ck_note_valid_range", "notes", type_="check")

    # Create new strict constraint: end_seconds > start_seconds (not >=)
    op.create_check_constraint(
        "ck_note_valid_range",
        "notes",
        "start_seconds IS NULL OR end_seconds IS NULL OR end_seconds > start_seconds",
    )


def downgrade() -> None:
    # Revert to the permissive constraint that allowed equality
    op.drop_constraint("ck_note_valid_range", "notes", type_="check")
    op.create_check_constraint(
        "ck_note_valid_range",
        "notes",
        "start_seconds IS NULL OR end_seconds IS NULL OR end_seconds >= start_seconds",
    )
