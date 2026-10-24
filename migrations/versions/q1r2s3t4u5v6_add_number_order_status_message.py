"""store diagnostic status messages for number orders

Revision ID: q1r2s3t4u5v6
Revises: p0q1r2s3t4u5
Create Date: 2026-10-24

The intent-first number purchase flow records why an intent was cancelled or
left in UNKNOWN state.  ``NumberOrder`` already writes this value, so the
column must exist on upgraded databases as well as newly-created ones.
"""

from alembic import op
import sqlalchemy as sa


revision = "q1r2s3t4u5v6"
down_revision = "p0q1r2s3t4u5"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in set(inspector.get_table_names()):
        return False
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("number_orders", "status_message"):
        with op.batch_alter_table("number_orders") as batch:
            batch.add_column(sa.Column("status_message", sa.String(length=500), nullable=True))


def downgrade() -> None:
    if _has_column("number_orders", "status_message"):
        with op.batch_alter_table("number_orders") as batch:
            batch.drop_column("status_message")
