"""Scope derivative ownership to media without changing technical identity.

Revision ID: 0014_derivative_media_identity
Revises: 0013_temporal_derivatives
"""

import sqlalchemy as sa

from alembic import op

revision = "0014_derivative_media_identity"
down_revision = "0013_temporal_derivatives"
branch_labels = None
depends_on = None

GLOBAL = "uq_temporal_derivatives_identity"
COMPOSITE = "uq_temporal_derivatives_media_identity"


def upgrade() -> None:
    connection = op.get_bind()
    # Also reconcile deployments that received the accidentally edited 0013.
    # Lock before introspection so constraints cannot change underneath us.
    connection.execute(sa.text("LOCK TABLE temporal_derivatives IN ACCESS EXCLUSIVE MODE"))
    constraints = {
        item["name"]: item["column_names"]
        for item in sa.inspect(connection).get_unique_constraints("temporal_derivatives")
    }
    if GLOBAL in constraints and constraints[GLOBAL] != ["identity"]:
        raise RuntimeError("Unexpected historical temporal derivative constraint")
    if COMPOSITE in constraints and constraints[COMPOSITE] != ["source_media_id", "identity"]:
        raise RuntimeError("Unexpected composite temporal derivative constraint")
    if GLOBAL not in constraints and COMPOSITE not in constraints:
        raise RuntimeError("Missing temporal derivative identity constraint")
    if GLOBAL in constraints:
        op.drop_constraint(GLOBAL, "temporal_derivatives", type_="unique")
    if COMPOSITE not in constraints:
        op.create_unique_constraint(COMPOSITE, "temporal_derivatives", ["source_media_id", "identity"])


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE temporal_derivatives IN ACCESS EXCLUSIVE MODE"))
    duplicate = connection.execute(sa.text(
        "SELECT identity FROM temporal_derivatives GROUP BY identity HAVING count(*) > 1 LIMIT 1"
    )).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot downgrade: duplicate cross-media technical identities are incompatible "
            "with historical UNIQUE(identity); no data or constraints were changed"
        )
    op.drop_constraint(COMPOSITE, "temporal_derivatives", type_="unique")
    op.create_unique_constraint(GLOBAL, "temporal_derivatives", ["identity"])
