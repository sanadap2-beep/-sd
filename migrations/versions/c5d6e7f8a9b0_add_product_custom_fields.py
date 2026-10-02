"""add custom input fields to products

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-10-02

يضيف حقول الإدخال المخصصة لجدول products:
- custom_input_label: اسم الحقل المخصص
- custom_input_placeholder: نص توضيحي داخل حقل الإدخال
- custom_description: وصف مخصص للمنتج
"""

from alembic import op
import sqlalchemy as sa

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("custom_input_label", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("custom_input_placeholder", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("custom_description", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "custom_description")
    op.drop_column("products", "custom_input_placeholder")
    op.drop_column("products", "custom_input_label")
