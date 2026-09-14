"""add WhatsApp section (bridge-linked daily packages)

Revision ID: f5e6d7c8b9a0
Revises: e1a2b3c4d5f6
Create Date: 2026-09-13

يضيف:
- wa_subscriptions: اشتراك المستخدم في قسم واتساب (صف واحد لكل مستخدم)
  مع حالة الربط وصلاحية الباقة والتجديد التلقائي.
- قيمة wa_subscription في transactions (لـ PostgreSQL فقط).
"""

from alembic import op
import sqlalchemy as sa

revision = "f5e6d7c8b9a0"
down_revision = "e1a2b3c4d5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE transactiontype ADD VALUE IF NOT EXISTS 'wa_subscription'")

    op.create_table(
        "wa_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("link_state", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("active_until", sa.DateTime(), nullable=True),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_package_days", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_renewed_at", sa.DateTime(), nullable=True),
        sa.Column("expire_notified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wa_subscriptions_user_id", "wa_subscriptions", ["user_id"], unique=True)
    op.create_index("ix_wa_subscriptions_link_state", "wa_subscriptions", ["link_state"])
    op.create_index("ix_wa_subscriptions_active_until", "wa_subscriptions", ["active_until"])


def downgrade() -> None:
    op.drop_index("ix_wa_subscriptions_active_until", table_name="wa_subscriptions")
    op.drop_index("ix_wa_subscriptions_link_state", table_name="wa_subscriptions")
    op.drop_index("ix_wa_subscriptions_user_id", table_name="wa_subscriptions")
    op.drop_table("wa_subscriptions")
