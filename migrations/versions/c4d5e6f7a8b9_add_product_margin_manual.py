"""add product margin_manual flag

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-09-04 09:30:00.000000

يتميّز الهامش اليدوي (الأدمن ضبطه) عن الهامش الضمني/التلقائي،
حتى يتحكم هامش القسم الفرعي بكل منتجاته تلقائياً ما لم يضبط الأدمن
المنتج بيده.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("margin_manual", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("products", "margin_manual")
