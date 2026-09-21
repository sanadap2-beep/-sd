"""add telegram ready sessions stock (manual file upload)

Revision ID: g1h2i3j4k5l6
Revises: f0a1b2c3d4e5
Create Date: 2026-09-21 00:00:00.000000

مخزون الجلسات الجاهزة (أرقام تلجرام الجاهزة):
- tg_ready_countries: الدول المفرزة تلقائياً من الملف (اسم+علم+سعر)
- tg_ready_batches: دفعات الرفع
- tg_ready_items: كل رقم/حساب (واحد = عملية بيع واحدة، ينقص تلقائياً)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g1h2i3j4k5l6"
down_revision: Union[str, None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tg_ready_countries",
        sa.Column("country_key", sa.String(length=16), primary_key=True),
        sa.Column("name_ar", sa.String(length=64), nullable=False),
        sa.Column("flag", sa.String(length=8), nullable=False, server_default="🌍"),
        sa.Column("price_usd", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("last_cost_usd", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("margin_percent", sa.Numeric(18, 4), nullable=False, server_default="50"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
    )
    op.create_table(
        "tg_ready_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_dupes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("margin_percent", sa.Numeric(18, 4), nullable=False, server_default="50"),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
    )
    op.create_table(
        "tg_ready_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("country_key", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("country_name_ar", sa.String(length=64), nullable=False, server_default="غير معروف"),
        sa.Column("flag", sa.String(length=8), nullable=False, server_default="🌍"),
        sa.Column("cost_usd", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("price_usd", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("payload_encrypted", sa.Text(), nullable=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("tg_ready_batches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.Enum("available", "sold", "void", name="tgreadyitemstatus"), nullable=False, server_default="available"),
        sa.Column("buyer_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("sold_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
    )
    op.create_index("ix_tg_ready_items_phone_number", "tg_ready_items", ["phone_number"], unique=True)
    op.create_index("ix_tg_ready_items_country_key", "tg_ready_items", ["country_key"])
    op.create_index("ix_tg_ready_items_status", "tg_ready_items", ["status"])


def downgrade() -> None:
    op.drop_index("ix_tg_ready_items_status", table_name="tg_ready_items")
    op.drop_index("ix_tg_ready_items_country_key", table_name="tg_ready_items")
    op.drop_index("ix_tg_ready_items_phone_number", table_name="tg_ready_items")
    op.drop_table("tg_ready_items")
    op.drop_table("tg_ready_batches")
    op.drop_table("tg_ready_countries")
    try:
        sa.Enum(name="tgreadyitemstatus").drop(op.get_bind(), checkfirst=True)
    except Exception:
        pass
