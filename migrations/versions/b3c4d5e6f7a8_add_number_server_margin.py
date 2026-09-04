"""add number_server profit margin

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-09-04 08:30:00.000000

إضافة نسبة ربح مستقلة لكل سيرفر في قسم الأرقام (مطابق لسيرفرات الأقسام).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("number_servers", sa.Column("margin_percent", sa.Numeric(18, 4), nullable=True))


def downgrade() -> None:
    op.drop_column("number_servers", "margin_percent")
