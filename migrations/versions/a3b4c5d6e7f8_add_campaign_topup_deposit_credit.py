"""add campaign codes + topup gift requests (diaspora growth features)

Revision ID: a3b4c5d6e7f8
Revises: f6a7b8c9d0e1
Create Date: 2026-09-16

يضيف جداول دفعة النمو/الشتات:
- campaign_codes / campaign_code_usages: أكواد الحملات مع تتبّع المصدر.
- topup_gift_requests: طلبات «اشحن لأهلك».
"""

from alembic import op
import sqlalchemy as sa

revision = "a3b4c5d6e7f8"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)


def upgrade() -> None:
    # ─── أكواد الحملات ───
    op.create_table(
        "campaign_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("tracking", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("discount_type", sa.String(length=16), nullable=False, server_default="percent"),
        sa.Column("discount_value", MONEY, nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("min_order_usd", MONEY, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_campaign_codes_code", "campaign_codes", ["code"])
    op.create_index("ix_campaign_codes_tracking", "campaign_codes", ["tracking"])

    op.create_table(
        "campaign_code_usages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaign_codes.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("discount_applied", MONEY, nullable=False),
        sa.Column("used_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "user_id", name="uq_campaign_user"),
    )
    op.create_index("ix_campaign_code_usages_campaign_id", "campaign_code_usages", ["campaign_id"])
    op.create_index("ix_campaign_code_usages_user_id", "campaign_code_usages", ["user_id"])

    # ─── طلبات اشحن لأهلك ───
    op.create_table(
        "topup_gift_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("operator", sa.String(length=32), nullable=False),
        sa.Column("recipient_number", sa.String(length=32), nullable=False),
        sa.Column("amount_usd", MONEY, nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("admin_chat_message_id", sa.BigInteger(), nullable=True),
        sa.Column("reject_reason", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topup_gift_requests_user_id", "topup_gift_requests", ["user_id"])
    op.create_index("ix_topup_gift_requests_status", "topup_gift_requests", ["status"])


def downgrade() -> None:
    op.drop_table("topup_gift_requests")
    op.drop_table("campaign_code_usages")
    op.drop_table("campaign_codes")