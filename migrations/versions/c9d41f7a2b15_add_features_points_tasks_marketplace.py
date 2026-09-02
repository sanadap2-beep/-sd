"""add feature flags, points economy, tasks, marketplace and refill tracking

Revision ID: c9d41f7a2b15
Revises: b7d1c4a2e9f3
Create Date: 2026-08-25

يضيف:
- feature_flags / feature_events: مركز التحكم بالإضافات وقياس استخدامها.
- tasks_catalog / task_progress / task_submissions: المهام مقابل النقاط.
- market_listings / market_listing_photos / market_transactions: سوق المستخدمين.
- عمودا refill_attempts و last_refill_check_at على unified_orders لضمان التعويض.

ملاحظة: create_all في init_db يُنشئ الجداول الجديدة تلقائياً، لكنه لا يضيف
أعمدة إلى جدول موجود، لذا عمودا unified_orders يحتاجان هذه الهجرة صراحةً.
"""

from alembic import op
import sqlalchemy as sa

revision = "c9d41f7a2b15"
down_revision = "b7d1c4a2e9f3"
branch_labels = None
depends_on = None

MONEY = sa.Numeric(18, 4)


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return False
    return column in {col["name"] for col in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # ── مركز التحكم بالإضافات ──
    op.create_table(
        "feature_flags",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "feature_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feature_key", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("value", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_feature_events_feature_key", "feature_events", ["feature_key"])
    op.create_index("ix_feature_events_event_type", "feature_events", ["event_type"])
    op.create_index("ix_feature_events_user_id", "feature_events", ["user_id"])
    op.create_index("ix_feature_events_created_at", "feature_events", ["created_at"])

    # ── المهام مقابل النقاط ──
    op.create_table(
        "tasks_catalog",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("title_ar", sa.String(length=128), nullable=False),
        sa.Column("title_en", sa.String(length=128), nullable=True),
        sa.Column("description_ar", sa.String(length=500), nullable=True),
        sa.Column("emoji", sa.String(length=8), nullable=False),
        sa.Column("task_type", sa.Enum(
            "DAILY_CHECKIN", "FIRST_DEPOSIT", "INVITE_FRIEND", "REVIEW_PRODUCT",
            "JOIN_CHANNEL", "REPORT_PROVIDER", "TRANSLATE_TEXT", "WATCH_AD",
            "PROFILE_COMPLETE", "CUSTOM", name="tasktype",
        ), nullable=False),
        sa.Column("reward_type", sa.Enum("POINTS", "BALANCE_USD", name="taskrewardtype"),
                  nullable=False),
        sa.Column("reward_points", sa.Integer(), nullable=False),
        sa.Column("reward_usd", MONEY, nullable=False),
        sa.Column("verification", sa.Enum(
            "NONE", "ONCE_PER_DAY", "ONCE_PER_USER", "ADMIN_APPROVAL",
            "TELEGRAM_MEMBERSHIP", "PROOF_ORDER", "CAPTCHA", "COOLDOWN_MINUTES",
            name="taskverification",
        ), nullable=False),
        sa.Column("verification_target", sa.String(length=255), nullable=True),
        sa.Column("daily_limit", sa.Integer(), nullable=False),
        sa.Column("total_limit", sa.Integer(), nullable=False),
        sa.Column("min_account_age_hours", sa.Integer(), nullable=False),
        sa.Column("min_orders_required", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_catalog_key", "tasks_catalog", ["key"], unique=True)

    op.create_table(
        "task_progress",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("period_key", sa.String(length=32), nullable=False),
        sa.Column("completions", sa.Integer(), nullable=False),
        sa.Column("points_earned", sa.Integer(), nullable=False),
        sa.Column("usd_earned", MONEY, nullable=False),
        sa.Column("last_completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks_catalog.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "task_id", "period_key", name="uq_task_progress"),
    )
    op.create_index("ix_task_progress_user_id", "task_progress", ["user_id"])
    op.create_index("ix_task_progress_task_id", "task_progress", ["task_id"])

    op.create_table(
        "task_submissions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("admin_note", sa.String(length=255), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks_catalog.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_submissions_user_id", "task_submissions", ["user_id"])
    op.create_index("ix_task_submissions_task_id", "task_submissions", ["task_id"])
    op.create_index("ix_task_submissions_status", "task_submissions", ["status"])

    # ── سوق المستخدمين ──
    op.create_table(
        "market_listings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("seller_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Enum(
            "GAME_ACCOUNT", "DIGITAL_CODE", "SMS_NUMBER", "SERVICE", "OTHER",
            name="marketlistingkind",
        ), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("seller_price_usd", MONEY, nullable=False),
        sa.Column("commission_percent", MONEY, nullable=False),
        sa.Column("secret_payload", sa.Text(), nullable=True),
        sa.Column("ownership_proof", sa.String(length=255), nullable=True),
        sa.Column("status", sa.Enum(
            "DRAFT", "PENDING_REVIEW", "APPROVED", "REJECTED", "SOLD",
            "CANCELLED", "EXPIRED", name="marketlistingstatus",
        ), nullable=False),
        sa.Column("rejection_reason", sa.String(length=255), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["seller_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_market_listings_seller_id", "market_listings", ["seller_id"])
    op.create_index("ix_market_listings_status", "market_listings", ["status"])
    op.create_index("ix_market_listings_created_at", "market_listings", ["created_at"])

    op.create_table(
        "market_listing_photos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["listing_id"], ["market_listings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_market_listing_photos_listing_id", "market_listing_photos", ["listing_id"]
    )

    op.create_table(
        "market_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("seller_id", sa.Integer(), nullable=False),
        sa.Column("buyer_id", sa.Integer(), nullable=False),
        sa.Column("seller_price_usd", MONEY, nullable=False),
        sa.Column("commission_percent", MONEY, nullable=False),
        sa.Column("commission_usd", MONEY, nullable=False),
        sa.Column("total_charged_usd", MONEY, nullable=False),
        sa.Column("points_used", sa.Integer(), nullable=False),
        sa.Column("points_usd", MONEY, nullable=False),
        sa.Column("status", sa.Enum(
            "AWAITING_BUYER", "FUNDED", "DELIVERED", "RELEASED", "REFUNDED", "DISPUTED",
            name="escrowstatus",
        ), nullable=False),
        sa.Column("delivered_at", sa.DateTime(), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("dispute_note", sa.String(length=500), nullable=True),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["listing_id"], ["market_listings.id"]),
        sa.ForeignKeyConstraint(["seller_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_market_transactions_listing_id", "market_transactions", ["listing_id"])
    op.create_index("ix_market_transactions_seller_id", "market_transactions", ["seller_id"])
    op.create_index("ix_market_transactions_buyer_id", "market_transactions", ["buyer_id"])
    op.create_index("ix_market_transactions_status", "market_transactions", ["status"])
    op.create_index("ix_market_transactions_created_at", "market_transactions", ["created_at"])

    # ── ضمان التعويض على الطلبات الموحدة ──
    if not _has_column(bind, "unified_orders", "refill_attempts"):
        op.add_column(
            "unified_orders",
            sa.Column("refill_attempts", sa.Integer(), nullable=False, server_default="0"),
        )
    if not _has_column(bind, "unified_orders", "last_refill_check_at"):
        op.add_column(
            "unified_orders",
            sa.Column("last_refill_check_at", sa.DateTime(), nullable=True),
        )
    # ملاحظة: لا نضيف قيد FK ذاتي هنا لأن SQLite لا يدعم ALTER للقيود،
    # والقيد معرَّف على النموذج نفسه فيكفي العمود والفهرس لعمل التطبيق.
    for _name, _col in (
        ("drip_parent_id", sa.Column("drip_parent_id", sa.Integer(), nullable=True)),
        ("drip_run_index", sa.Column("drip_run_index", sa.Integer(), nullable=True)),
        ("drip_total_runs", sa.Column("drip_total_runs", sa.Integer(), nullable=True)),
        ("drip_scheduled_for", sa.Column("drip_scheduled_for", sa.DateTime(), nullable=True)),
    ):
        if not _has_column(bind, "unified_orders", _name):
            op.add_column("unified_orders", _col)
    op.create_index(
        "ix_unified_orders_drip_parent_id", "unified_orders", ["drip_parent_id"]
    )
    op.create_index(
        "ix_unified_orders_drip_scheduled_for", "unified_orders", ["drip_scheduled_for"]
    )
    op.create_table(
        "unified_refunds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("amount_usd", MONEY, nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for _c in ("user_id", "source", "reason", "created_at"):
        op.create_index(f"ix_unified_refunds_{_c}", "unified_refunds", [_c])

    op.create_table(
        "warm_pool_numbers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Enum(
            "FIVESIM", "HEROSMS", "SMS_ACTIVATE", "SMSHUB", name="providername"
        ), nullable=False),
        sa.Column("provider_order_id", sa.String(length=64), nullable=False),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("service_code", sa.String(length=32), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("cost_usd", MONEY, nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    for _c in ("phone_number", "service_code", "country_code", "expires_at"):
        op.create_index(f"ix_warm_pool_numbers_{_c}", "warm_pool_numbers", [_c])

    op.create_table(
        "revenue_share_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("issuer_id", sa.Integer(), nullable=False),
        sa.Column("holder_id", sa.Integer(), nullable=True),
        sa.Column("share_percent", sa.Integer(), nullable=False),
        sa.Column("price_usd", MONEY, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["issuer_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["holder_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_revenue_share_tokens_issuer_id", "revenue_share_tokens", ["issuer_id"])
    op.create_index("ix_revenue_share_tokens_status", "revenue_share_tokens", ["status"])

    op.create_table(
        "purchase_rooms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("creator_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("target_quantity", sa.Integer(), nullable=False),
        sa.Column("members", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_purchase_rooms_creator_id", "purchase_rooms", ["creator_id"])
    op.create_index("ix_purchase_rooms_product_id", "purchase_rooms", ["product_id"])
    op.create_index("ix_purchase_rooms_status", "purchase_rooms", ["status"])

    op.create_table(
        "experiment_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("experiment", sa.String(length=64), nullable=False),
        sa.Column("variant", sa.String(length=32), nullable=False),
        sa.Column("converted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_experiment_results_experiment", "experiment_results", ["experiment"])
    op.create_index("ix_experiment_results_variant", "experiment_results", ["variant"])

    op.create_table(
        "jurisdiction_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("blocked_services", sa.Text(), nullable=True),
        sa.Column("requires_kyc_tier", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jurisdiction_rules_country_code", "jurisdiction_rules", ["country_code"])

    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("brand_name", sa.String(length=128), nullable=False),
        sa.Column("bot_username", sa.String(length=64), nullable=True),
        sa.Column("default_language", sa.String(length=8), nullable=False),
        sa.Column("display_currency", sa.String(length=8), nullable=False),
        sa.Column("commission_percent", MONEY, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"], unique=True)
    op.create_index("ix_tenants_bot_username", "tenants", ["bot_username"], unique=True)

    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("secret", sa.String(length=128), nullable=False),
        sa.Column("scopes", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_endpoints_user_id", "webhook_endpoints", ["user_id"])

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("endpoint_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["endpoint_id"], ["webhook_endpoints.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_deliveries_endpoint_id", "webhook_deliveries", ["endpoint_id"])
    op.create_index("ix_webhook_deliveries_created_at", "webhook_deliveries", ["created_at"])

    op.create_table(
        "provider_bids",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("service_code", sa.String(length=32), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("price_usd", MONEY, nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["provider_id"], ["api_providers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for _c in ("provider_id", "service_code", "country_code", "expires_at"):
        op.create_index(f"ix_provider_bids_{_c}", "provider_bids", [_c])

    op.create_table(
        "autonomous_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("service_code", sa.String(length=32), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("quantity_per_run", sa.Integer(), nullable=False),
        sa.Column("total_runs", sa.Integer(), nullable=False),
        sa.Column("completed_runs", sa.Integer(), nullable=False),
        sa.Column("interval_hours", sa.Integer(), nullable=False),
        sa.Column("max_unit_price_usd", MONEY, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("next_run_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_autonomous_jobs_user_id", "autonomous_jobs", ["user_id"])
    op.create_index("ix_autonomous_jobs_status", "autonomous_jobs", ["status"])
    op.create_index("ix_autonomous_jobs_next_run_at", "autonomous_jobs", ["next_run_at"])

    op.create_table(
        "escrow_holds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payer_id", sa.Integer(), nullable=False),
        sa.Column("amount_usd", MONEY, nullable=False),
        sa.Column("commission_usd", MONEY, nullable=False),
        sa.Column("purpose", sa.String(length=128), nullable=True),
        sa.Column("reference", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["payer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_escrow_holds_payer_id", "escrow_holds", ["payer_id"])
    op.create_index("ix_escrow_holds_status", "escrow_holds", ["status"])
    op.create_index("ix_escrow_holds_created_at", "escrow_holds", ["created_at"])

    op.create_table(
        "p2p_code_listings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("seller_id", sa.Integer(), nullable=False),
        sa.Column("buyer_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("price_usd", MONEY, nullable=False),
        sa.Column("commission_usd", MONEY, nullable=False),
        sa.Column("encrypted_code", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["seller_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["buyer_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_p2p_code_listings_seller_id", "p2p_code_listings", ["seller_id"])
    op.create_index("ix_p2p_code_listings_status", "p2p_code_listings", ["status"])

    if not _has_column(bind, "number_orders", "portable_until"):
        op.add_column(
            "number_orders", sa.Column("portable_until", sa.DateTime(), nullable=True)
        )
    op.create_index(
        "ix_number_orders_portable_until", "number_orders", ["portable_until"]
    )

    op.create_table(
        "price_limit_orders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("service_code", sa.String(length=32), nullable=False),
        sa.Column("country_code", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("target_price_usd", MONEY, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("triggered_at", sa.DateTime(), nullable=True),
        sa.Column("triggered_price_usd", MONEY, nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for _col in ("user_id", "status", "service_code", "country_code"):
        op.create_index(
            f"ix_price_limit_orders_{_col}", "price_limit_orders", [_col]
        )

    op.create_table(
        "vip_certificates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("phone_number", sa.String(length=32), nullable=False),
        sa.Column("serial", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("transferred_at", sa.DateTime(), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vip_certificates_owner_id", "vip_certificates", ["owner_id"])
    op.create_index(
        "ix_vip_certificates_phone_number", "vip_certificates", ["phone_number"], unique=True
    )
    op.create_index("ix_vip_certificates_serial", "vip_certificates", ["serial"], unique=True)

    if not _has_column(bind, "number_orders", "dedicated_until"):
        op.add_column(
            "number_orders", sa.Column("dedicated_until", sa.DateTime(), nullable=True)
        )
    op.create_index(
        "ix_number_orders_dedicated_until", "number_orders", ["dedicated_until"]
    )
    # user_id يصبح قابلاً للفراغ لأن البركة المُسخَّنة تحمل أرقاماً بلا مالك.
    # SQLite لا يدعم تعديل القيود، فالقيد يبقى كما أنشئ والجديد يُنشأ صحيحاً.

    if not _has_column(bind, "number_orders", "number_fingerprint"):
        op.add_column(
            "number_orders",
            sa.Column("number_fingerprint", sa.String(length=32), nullable=True),
        )
    op.create_index(
        "ix_number_orders_number_fingerprint", "number_orders", ["number_fingerprint"]
    )
    if not _has_column(bind, "users", "verification_tier"):
        op.add_column(
            "users",
            sa.Column("verification_tier", sa.Integer(), nullable=False, server_default="0"),
        )

    if not _has_column(bind, "number_orders", "insurance_claimed"):
        op.add_column(
            "number_orders",
            sa.Column("insurance_claimed", sa.Boolean(), nullable=False, server_default="0"),
        )
    if not _has_column(bind, "unified_orders", "key_swaps"):
        op.add_column(
            "unified_orders",
            sa.Column("key_swaps", sa.Integer(), nullable=False, server_default="0"),
        )

    # ── الاشتراكات و Failover الكتالوج ──
    op.create_table(
        "product_provider_routes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("api_provider_id", sa.Integer(), nullable=False),
        sa.Column("provider_service_id", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["api_provider_id"], ["api_providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "api_provider_id", name="uq_product_provider_route"),
    )
    op.create_index(
        "ix_product_provider_routes_product_id", "product_provider_routes", ["product_id"]
    )
    op.create_index(
        "ix_product_provider_routes_api_provider_id",
        "product_provider_routes", ["api_provider_id"],
    )

    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("price_usd", MONEY, nullable=False),
        sa.Column("auto_renew_default", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subscription_plans_product_id", "subscription_plans", ["product_id"])

    op.create_table(
        "user_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("auto_renew", sa.Boolean(), nullable=False),
        sa.Column("last_reminded_days_left", sa.Integer(), nullable=True),
        sa.Column("is_cancelled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["subscription_plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_subscriptions_user_id", "user_subscriptions", ["user_id"])
    op.create_index("ix_user_subscriptions_product_id", "user_subscriptions", ["product_id"])
    op.create_index("ix_user_subscriptions_expires_at", "user_subscriptions", ["expires_at"])


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("ix_user_subscriptions_expires_at", table_name="user_subscriptions")
    op.drop_index("ix_user_subscriptions_product_id", table_name="user_subscriptions")
    op.drop_index("ix_user_subscriptions_user_id", table_name="user_subscriptions")
    op.drop_table("user_subscriptions")
    op.drop_index("ix_subscription_plans_product_id", table_name="subscription_plans")
    op.drop_table("subscription_plans")
    op.drop_index(
        "ix_product_provider_routes_api_provider_id", table_name="product_provider_routes"
    )
    op.drop_index("ix_product_provider_routes_product_id", table_name="product_provider_routes")
    op.drop_table("product_provider_routes")

    op.drop_index("ix_unified_orders_drip_scheduled_for", table_name="unified_orders")
    op.drop_index("ix_unified_orders_drip_parent_id", table_name="unified_orders")
    for _name in ("drip_scheduled_for", "drip_total_runs", "drip_run_index", "drip_parent_id"):
        if _has_column(bind, "unified_orders", _name):
            op.drop_column("unified_orders", _name)

    op.drop_index("ix_experiment_results_variant", table_name="experiment_results")
    op.drop_index("ix_experiment_results_experiment", table_name="experiment_results")
    op.drop_table("experiment_results")
    op.drop_index("ix_purchase_rooms_status", table_name="purchase_rooms")
    op.drop_index("ix_purchase_rooms_product_id", table_name="purchase_rooms")
    op.drop_index("ix_purchase_rooms_creator_id", table_name="purchase_rooms")
    op.drop_table("purchase_rooms")
    for _c in ("created_at", "reason", "source", "user_id"):
        op.drop_index(f"ix_unified_refunds_{_c}", table_name="unified_refunds")
    op.drop_table("unified_refunds")

    for _c in ("expires_at", "country_code", "service_code", "phone_number"):
        op.drop_index(f"ix_warm_pool_numbers_{_c}", table_name="warm_pool_numbers")
    op.drop_table("warm_pool_numbers")

    op.drop_index("ix_revenue_share_tokens_status", table_name="revenue_share_tokens")
    op.drop_index("ix_revenue_share_tokens_issuer_id", table_name="revenue_share_tokens")
    op.drop_table("revenue_share_tokens")

    for _c in ("expires_at", "country_code", "service_code", "provider_id"):
        op.drop_index(f"ix_provider_bids_{_c}", table_name="provider_bids")
    op.drop_table("provider_bids")
    op.drop_index("ix_webhook_deliveries_created_at", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_endpoint_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_endpoints_user_id", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")
    op.drop_index("ix_tenants_bot_username", table_name="tenants")
    op.drop_index("ix_tenants_slug", table_name="tenants")
    op.drop_table("tenants")
    op.drop_index("ix_jurisdiction_rules_country_code", table_name="jurisdiction_rules")
    op.drop_table("jurisdiction_rules")

    op.drop_index("ix_autonomous_jobs_next_run_at", table_name="autonomous_jobs")
    op.drop_index("ix_autonomous_jobs_status", table_name="autonomous_jobs")
    op.drop_index("ix_autonomous_jobs_user_id", table_name="autonomous_jobs")
    op.drop_table("autonomous_jobs")

    op.drop_index("ix_p2p_code_listings_status", table_name="p2p_code_listings")
    op.drop_index("ix_p2p_code_listings_seller_id", table_name="p2p_code_listings")
    op.drop_table("p2p_code_listings")
    op.drop_index("ix_escrow_holds_created_at", table_name="escrow_holds")
    op.drop_index("ix_escrow_holds_status", table_name="escrow_holds")
    op.drop_index("ix_escrow_holds_payer_id", table_name="escrow_holds")
    op.drop_table("escrow_holds")

    op.drop_index("ix_vip_certificates_serial", table_name="vip_certificates")
    op.drop_index("ix_vip_certificates_phone_number", table_name="vip_certificates")
    op.drop_index("ix_vip_certificates_owner_id", table_name="vip_certificates")
    op.drop_table("vip_certificates")
    for _col in ("country_code", "service_code", "status", "user_id"):
        op.drop_index(f"ix_price_limit_orders_{_col}", table_name="price_limit_orders")
    op.drop_table("price_limit_orders")
    op.drop_index("ix_number_orders_portable_until", table_name="number_orders")
    if _has_column(bind, "number_orders", "portable_until"):
        op.drop_column("number_orders", "portable_until")

    op.drop_index("ix_number_orders_dedicated_until", table_name="number_orders")
    if _has_column(bind, "number_orders", "dedicated_until"):
        op.drop_column("number_orders", "dedicated_until")
    op.drop_index("ix_number_orders_number_fingerprint", table_name="number_orders")
    if _has_column(bind, "number_orders", "number_fingerprint"):
        op.drop_column("number_orders", "number_fingerprint")
    if _has_column(bind, "users", "verification_tier"):
        op.drop_column("users", "verification_tier")

    if _has_column(bind, "number_orders", "insurance_claimed"):
        op.drop_column("number_orders", "insurance_claimed")
    if _has_column(bind, "unified_orders", "key_swaps"):
        op.drop_column("unified_orders", "key_swaps")
    if _has_column(bind, "unified_orders", "last_refill_check_at"):
        op.drop_column("unified_orders", "last_refill_check_at")
    if _has_column(bind, "unified_orders", "refill_attempts"):
        op.drop_column("unified_orders", "refill_attempts")

    op.drop_index("ix_market_transactions_created_at", table_name="market_transactions")
    op.drop_index("ix_market_transactions_status", table_name="market_transactions")
    op.drop_index("ix_market_transactions_buyer_id", table_name="market_transactions")
    op.drop_index("ix_market_transactions_seller_id", table_name="market_transactions")
    op.drop_index("ix_market_transactions_listing_id", table_name="market_transactions")
    op.drop_table("market_transactions")
    op.drop_index("ix_market_listing_photos_listing_id", table_name="market_listing_photos")
    op.drop_table("market_listing_photos")
    op.drop_index("ix_market_listings_created_at", table_name="market_listings")
    op.drop_index("ix_market_listings_status", table_name="market_listings")
    op.drop_index("ix_market_listings_seller_id", table_name="market_listings")
    op.drop_table("market_listings")

    op.drop_index("ix_task_submissions_status", table_name="task_submissions")
    op.drop_index("ix_task_submissions_task_id", table_name="task_submissions")
    op.drop_index("ix_task_submissions_user_id", table_name="task_submissions")
    op.drop_table("task_submissions")
    op.drop_index("ix_task_progress_task_id", table_name="task_progress")
    op.drop_index("ix_task_progress_user_id", table_name="task_progress")
    op.drop_table("task_progress")
    op.drop_index("ix_tasks_catalog_key", table_name="tasks_catalog")
    op.drop_table("tasks_catalog")

    op.drop_index("ix_feature_events_created_at", table_name="feature_events")
    op.drop_index("ix_feature_events_user_id", table_name="feature_events")
    op.drop_index("ix_feature_events_event_type", table_name="feature_events")
    op.drop_index("ix_feature_events_feature_key", table_name="feature_events")
    op.drop_table("feature_events")
    op.drop_table("feature_flags")

    for enum_name in (
        "escrowstatus", "marketlistingstatus", "marketlistingkind",
        "taskverification", "taskrewardtype", "tasktype",
    ):
        sa.Enum(name=enum_name).drop(bind, checkfirst=True)
