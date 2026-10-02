"""add quantity to topup_gift_requests

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-10-02

يضيف عمود quantity لجدول topup_gift_requests لتخزين الكمية المختارة من المزود.
"""

from alembic import op
import sqlalchemy as sa

revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)


def upgrade() -> None:
    op.add_column(
        "topup_gift_requests",
        sa.Column("quantity", MONEY, nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("topup_gift_requests", "quantity")
