"""add cart reminder timestamp

Revision ID: j1k2l3m4n5o6
Revises: i9j0k1l2m3n4
Create Date: 2026-10-03

يضيف عمود ``reminder_sent_at`` لعناصر السلة حتى نرسل **تذكيراً واحداً**
لكل سلة متروكة: لا يُعاد التذكير إلا إذا تغيّرت السلة بعده
(``updated_at`` أحدث من ``reminder_sent_at``).

العمود اختياري (nullable) ولا يغيّر أي سلوك قائم.
"""

from alembic import op
import sqlalchemy as sa


revision = "j1k2l3m4n5o6"
down_revision = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("cart_items", "reminder_sent_at"):
        op.add_column(
            "cart_items",
            sa.Column("reminder_sent_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("cart_items", "reminder_sent_at"):
        op.drop_column("cart_items", "reminder_sent_at")
