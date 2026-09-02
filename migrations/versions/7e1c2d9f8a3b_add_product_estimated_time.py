"""add estimated completion time to products

Revision ID: 7e1c2d9f8a3b
Revises: c9d41f7a2b15
Create Date: 2026-09-02

- عمود جديد products.estimated_time (نص اختياري) لعرض الوقت التقريبي
  لاستكمال الطلب للمستخدم (مثال: "5-30 دقيقة" / "1-3 ساعات").
"""

from alembic import op
import sqlalchemy as sa

revision = "7e1c2d9f8a3b"
down_revision = "c9d41f7a2b15"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.add_column(
            sa.Column("estimated_time", sa.String(64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_column("estimated_time")
