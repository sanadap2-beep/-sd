"""add missing provider enum labels

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-10-04 00:00:00.000000

Adds the missing PostgreSQL enum labels that were introduced in the model code
but never added to the database enum type. This prevents provider lookups like
SMSPOOL / GRIZZLY from failing during startup and migration seeding.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "l2m3n4o5p6q7"
down_revision: Union[str, None] = "k1l2m3n4o5p6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for label in ("SMSPOOL", "GRIZZLY"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM pg_enum e
                    JOIN pg_type t ON t.oid = e.enumtypid
                    WHERE t.typname = 'providername' AND e.enumlabel = '{label}'
                ) THEN
                    ALTER TYPE providername ADD VALUE '{label}';
                END IF;
            END $$;
            """
        )


def downgrade() -> None:
    # PostgreSQL does not support removing enum values in-place without recreating
    # the type, which is unsafe for live data. Keep this migration non-destructive.
    pass
