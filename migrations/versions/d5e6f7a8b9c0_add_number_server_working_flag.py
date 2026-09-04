"""add number_server is_working flag (green dot)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-09-05 09:00:00.000000

النقطة الخضراء أمام السيرفر الشغّال في قسم الأرقام.

- سيرفرات قسم الأرقام تُعرض للمستخدم بأسماء محايدة (سيرفر 1، سيرفر 2 ...)
  ولا تكشف اسم المزود.
- ``is_working`` علامة يضبطها الأدمن يدوياً على السيرفر الذي يعمل فعلياً،
  فتظهر أمامه 🟢 للمستخدم. مستقلة عن ``is_active``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "number_servers",
        sa.Column("is_working", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("number_servers", "is_working")
