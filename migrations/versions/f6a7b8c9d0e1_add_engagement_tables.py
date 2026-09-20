"""add engagement tables (spin wheel, challenges, resale, reviews, alerts, deposit bonuses)

Revision ID: f6a7b8c9d0e1
Revises: f5e6d7c8b9a0
Create Date: 2026-09-16

يضيف جداول ميزات التفاعل الجديدة:
- deposit_bonus_rules / deposit_bonus_grants: مكافآت الشحن.
- spin_prizes / spin_history: عجلة الحظ اليومية.
- price_alerts: إنذارات انخفاض السعر.
- weekly_challenges / weekly_challenge_progress: التحديات الأسبوعية.
- number_resale_listings: سوق الأرقام المستعملة.
- provider_reviews: تقييم المزودين بعد الطلب.
"""

from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "f5e6d7c8b9a0"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)


def upgrade() -> None:
    # ─── مكافآت الشحن ───
    op.create_table(
        "deposit_bonus_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("min_deposit_usd", MONEY, nullable=False),
        sa.Column("bonus_percent", MONEY, nullable=False, server_default="5"),
        sa.Column("max_bonus_usd", MONEY, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "deposit_bonus_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("deposit_id", sa.Integer(), nullable=True),
        sa.Column("deposit_source", sa.String(length=32), nullable=False, server_default="deposit"),
        sa.Column("deposit_amount_usd", MONEY, nullable=False),
        sa.Column("bonus_usd", MONEY, nullable=False),
        sa.Column("rule_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deposit_bonus_grants_user_id", "deposit_bonus_grants", ["user_id"])
    op.create_index("ix_deposit_bonus_grants_deposit_id", "deposit_bonus_grants", ["deposit_id"])

    # ─── عجلة الحظ ───
    op.create_table(
        "spin_prizes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name_ar", sa.String(length=128), nullable=False),
        sa.Column("prize_type", sa.String(length=24), nullable=False, server_default="balance"),
        sa.Column("value", MONEY, nullable=False, server_default="0"),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "spin_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("prize_id", sa.Integer(), nullable=True),
        sa.Column("prize_type", sa.String(length=24), nullable=False),
        sa.Column("prize_label", sa.String(length=128), nullable=False),
        sa.Column("value", MONEY, nullable=False, server_default="0"),
        sa.Column("day_key", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_spin_history_user_id", "spin_history", ["user_id"])
    op.create_index("ix_spin_history_day_key", "spin_history", ["day_key"])

    # ─── إنذارات السعر ───
    op.create_table(
        "price_alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("service_code", sa.String(length=32), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("target_price_usd", MONEY, nullable=False),
        sa.Column("last_checked_price", MONEY, nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("last_triggered_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_alerts_user_id", "price_alerts", ["user_id"])
    op.create_index("ix_price_alerts_service_code", "price_alerts", ["service_code"])
    op.create_index("ix_price_alerts_country_code", "price_alerts", ["country_code"])
    op.create_index("ix_price_alerts_status", "price_alerts", ["status"])

    # ─── التحديات الأسبوعية ───
    op.create_table(
        "weekly_challenges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("emoji", sa.String(length=8), nullable=False, server_default="🏆"),
        sa.Column("metric", sa.String(length=32), nullable=False, server_default="orders"),
        sa.Column("target_value", MONEY, nullable=False, server_default="10"),
        sa.Column("reward_usd", MONEY, nullable=False, server_default="1"),
        sa.Column("reward_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("week_start", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_weekly_challenges_week_start", "weekly_challenges", ["week_start"])
    op.create_table(
        "weekly_challenge_progress",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("challenge_id", sa.Integer(), sa.ForeignKey("weekly_challenges.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("progress", MONEY, nullable=False, server_default="0"),
        sa.Column("claimed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("challenge_id", "user_id", name="uq_weekly_challenge_user"),
    )
    op.create_index("ix_weekly_challenge_progress_challenge_id", "weekly_challenge_progress", ["challenge_id"])
    op.create_index("ix_weekly_challenge_progress_user_id", "weekly_challenge_progress", ["user_id"])

    # ─── سوق الأرقام المستعملة ───
    op.create_table(
        "number_resale_listings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("seller_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("buyer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("source_order_id", sa.Integer(), nullable=False),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("service_name", sa.String(length=64), nullable=False),
        sa.Column("country_name", sa.String(length=64), nullable=False),
        sa.Column("price_usd", MONEY, nullable=False),
        sa.Column("commission_usd", MONEY, nullable=False, server_default="0"),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("sold_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_number_resale_listings_seller_id", "number_resale_listings", ["seller_id"])
    op.create_index("ix_number_resale_listings_source_order_id", "number_resale_listings", ["source_order_id"])
    op.create_index("ix_number_resale_listings_status", "number_resale_listings", ["status"])

    # ─── تقييم المزودين ───
    op.create_table(
        "provider_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False, server_default="number"),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_provider_reviews_user_id", "provider_reviews", ["user_id"])
    op.create_index("ix_provider_reviews_provider", "provider_reviews", ["provider"])
    op.create_index("ix_provider_reviews_order_id", "provider_reviews", ["order_id"])


def downgrade() -> None:
    op.drop_table("provider_reviews")
    op.drop_table("number_resale_listings")
    op.drop_table("weekly_challenge_progress")
    op.drop_table("weekly_challenges")
    op.drop_table("price_alerts")
    op.drop_table("spin_history")
    op.drop_table("spin_prizes")
    op.drop_table("deposit_bonus_grants")
    op.drop_table("deposit_bonus_rules")