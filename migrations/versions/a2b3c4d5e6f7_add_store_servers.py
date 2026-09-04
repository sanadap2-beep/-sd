"""add store servers (multi-provider servers for any section)

Revision ID: a2b3c4d5e6f7
Revises: f1e2d3c4b5a6
Create Date: 2026-09-04 07:30:00.000000

سيرفرات عامة ترتبط بأي قسم (رئيسي/فرعي/رقم/عام) وبمزود محدد، مع
نسبة ربح مستقلة لكل سيرفر.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "f1e2d3c4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "store_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scope", sa.String(length=32), nullable=False, index=True),
        sa.Column("scope_id", sa.Integer(), nullable=False, index=True),
        sa.Column("provider_kind", sa.String(length=16), nullable=False, server_default="api"),
        sa.Column("provider_value", sa.String(length=64), nullable=True),
        sa.Column(
            "api_provider_id",
            sa.Integer(),
            sa.ForeignKey("api_providers.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("name_ar", sa.String(length=96), nullable=False),
        sa.Column("emoji", sa.String(length=8), nullable=False, server_default="🖥"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("margin_percent", sa.Numeric(18, 4), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
    )


def downgrade() -> None:
    op.drop_index("ix_store_servers_api_provider_id", table_name="store_servers")
    op.drop_index("ix_store_servers_scope_id", table_name="store_servers")
    op.drop_index("ix_store_servers_scope", table_name="store_servers")
    op.drop_table("store_servers")
