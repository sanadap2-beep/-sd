"""add number servers provider selection

Revision ID: f1e2d3c4b5a6
Revises: e7f3a9c2b1d4
Create Date: 2026-09-04 06:00:00.000000

السيرفرات/المزودين الديناميكية لخدمة الأرقام.

- number_servers: كل خدمة أرقام تحوي عدة سيرفرات، كل سيرفر مربوط بمزود
  معين، ويختاره المستخدم قبل رؤية الدول.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1e2d3c4b5a6"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "number_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "number_service_id",
            sa.Integer(),
            sa.ForeignKey("number_services.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("provider", sa.String(length=64), nullable=False, index=True),
        sa.Column("name_ar", sa.String(length=96), nullable=False),
        sa.Column("emoji", sa.String(length=8), nullable=False, server_default="🖥"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
    )


def downgrade() -> None:
    op.drop_index("ix_number_servers_provider", table_name="number_servers")
    op.drop_index("ix_number_servers_number_service_id", table_name="number_servers")
    op.drop_table("number_servers")
