"""add per-section profit margins (categories + sub_categories)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-09-03 13:00:00.000000

هوامش الربح على مستوى القسم:
- categories.profit_margin_percent: هامش القسم الرئيسي.
- sub_categories.profit_margin_percent: هامش القسم الفرعي (وأقسام الرشق الداخلية).
كلاهما null = يرث الهامش الأعلى (القسم → العالمي default_profit_margin_percent).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column("profit_margin_percent", sa.Numeric(18, 4), nullable=True),
    )
    op.add_column(
        "sub_categories",
        sa.Column("profit_margin_percent", sa.Numeric(18, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sub_categories", "profit_margin_percent")
    op.drop_column("categories", "profit_margin_percent")
