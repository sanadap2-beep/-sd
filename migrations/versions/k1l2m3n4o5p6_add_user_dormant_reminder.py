"""add dormant-user reminder timestamp

Revision ID: k1l2m3n4o5p6
Revises: j1k2l3m4n5o6
Create Date: 2026-10-03

يضيف عمود ``dormant_reminder_at`` للمستخدمين حتى نرسل تذكير «اشتقنا لك»
لمرة واحدة لكل مستخدم نائم، وننتظر فترة التهدئة قبل إعادته.

العمود اختياري (nullable) ولا يغيّر أي سلوك قائم.
"""

from alembic import op
import sqlalchemy as sa


revision = "k1l2m3n4o5p6"
down_revision = "j1k2l3m4n5o6"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("users", "dormant_reminder_at"):
        op.add_column(
            "users",
            sa.Column("dormant_reminder_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("users", "dormant_reminder_at"):
        op.drop_column("users", "dormant_reminder_at")
