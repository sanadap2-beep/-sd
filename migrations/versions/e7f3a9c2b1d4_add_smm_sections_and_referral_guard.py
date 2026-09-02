"""add smm inner sections and referral bot guard

Revision ID: e7f3a9c2b1d4
Revises: 7e1c2d9f8a3b
Create Date: 2026-09-03 10:00:00.000000

يضيف:
- sub_categories.parent_sub_category_id + kind_key → أقسام داخلية داخل
  تطبيقات قسم الرشق (متابعون/لايكات/مشاهدات...).
- users.referral_check_pending + referral_check_fails → حماية الإحالة من
  البوتات (اختبار بشري قبل تفعيل المكافأة).
- products.is_auto_published → تمييز المنتجات المنشأة تلقائياً من أول 5
  خدمات مسحوبة (للأرخص) داخل كل قسم.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7f3a9c2b1d4"
down_revision: Union[str, None] = "7e1c2d9f8a3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # أقسام داخلية: القسم الفرعي يمكن أن يكون أباً لأقسام داخلية (sections).
    op.add_column(
        "sub_categories",
        sa.Column("parent_sub_category_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_sub_categories_parent_sub_category_id"),
        "sub_categories",
        ["parent_sub_category_id"],
        unique=False,
    )
    op.add_column(
        "sub_categories",
        sa.Column("kind_key", sa.String(length=32), nullable=True),
    )
    op.create_index(
        op.f("ix_sub_categories_kind_key"),
        "sub_categories",
        ["kind_key"],
        unique=False,
    )

    # حماية الإحالة من البوتات.
    op.add_column(
        "users",
        sa.Column("referral_check_pending", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "users",
        sa.Column("referral_check_fails", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    # تمييز المنتجات المنشأة تلقائياً.
    op.add_column(
        "products",
        sa.Column("is_auto_published", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("products", "is_auto_published")
    op.drop_column("users", "referral_check_fails")
    op.drop_column("users", "referral_check_pending")
    op.drop_index(op.f("ix_sub_categories_kind_key"), table_name="sub_categories")
    op.drop_column("sub_categories", "kind_key")
    op.drop_index(op.f("ix_sub_categories_parent_sub_category_id"), table_name="sub_categories")
    op.drop_column("sub_categories", "parent_sub_category_id")
