"""make unified_orders.product_id nullable

Revision ID: e8f9a0b1c2d3
Revises: d5e6f7a8b9c0
Create Date: 2026-09-09 09:00:00.000000

طلبات «التطبيقات والأكواد الجاهزة» لا ترتبط بمنتج حقيقي في جدول products.
كانت تُسجَّل سابقاً بـ product_id=0 ففشل قيد FOREIGN KEY. أصبح الحقل
قابلاً للفراغ ونُسجّل فيه NULL بدل 0.

نستخدم batch_alter_table لأن SQLite لا يدعم ALTER COLUMN مباشرةً.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "d5e6f7a8b9c0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("unified_orders") as batch_op:
        batch_op.alter_column(
            "product_id",
            existing_type=sa.Integer(),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("unified_orders") as batch_op:
        batch_op.alter_column(
            "product_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
