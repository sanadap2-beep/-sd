"""add category description

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-03 14:00:00.000000

categories.description: شرح القسم الرئيسي الذي يظهر للزبون عند فتح القسم
(لكل قسم وخدمة: المنتجات لها description أصلاً، والأقسام الفرعية كذلك).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column("description", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("categories", "description")
