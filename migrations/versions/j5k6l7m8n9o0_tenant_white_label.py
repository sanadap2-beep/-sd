"""white-label tenants: tenant tables + tenant_id scoping + composite user unique

Revision ID: j5k6l7m8n9o0
Revises: i4j5k6l7m8n9
Create Date: 2026-10-04

- جداول جديدة: tenants, tenant_wallets, tenant_category_margins,
  tenant_catalog_selection, tenant_orders_map.
- عمود tenant_id (افتراضي 0 = البوت الأساسي) على:
  users, unified_orders, transactions, support_tickets,
  deposit_requests, number_orders.
- users: إسقاط القيد الفريد العام على telegram_id واستبداله
  بقيد مركب (tenant_id, telegram_id) لعزل المستأجرين.
"""

from alembic import op
import sqlalchemy as sa

revision = "j5k6l7m8n9"
down_revision = "i4j5k6l7m8n9"
branch_labels = None
depends_on = None


TENANT_TABLES = (
    "users",
    "unified_orders",
    "transactions",
    "support_tickets",
    "deposit_requests",
    "number_orders",
)


def _table_exists(insp, name: str) -> bool:
    return name in set(insp.get_table_names())


def _column_names(insp, table: str) -> set:
    try:
        return {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return set()


def _create_tenant_tables() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    # جدول tenants موجود مسبقاً (مصنع العلامات) — نضيف الأعمدة الجديدة فقط.
    if "tenants" in existing:
        cols = _column_names(insp, "tenants")
        new_cols = {
            "owner_user_id": sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", name="fk_tenants_owner"), nullable=True),
            "token_encrypted": sa.Column("token_encrypted", sa.Text(), nullable=True),
            "token_hash": sa.Column("token_hash", sa.String(64), nullable=True),
            "margin_percent": sa.Column("margin_percent", sa.Numeric(18, 4), nullable=False, server_default="20"),
            "platform_fee_percent": sa.Column("platform_fee_percent", sa.Numeric(18, 4), nullable=False, server_default="2"),
            "catalog_mode": sa.Column("catalog_mode", sa.String(16), nullable=False, server_default="full"),
            "subscription_status": sa.Column("subscription_status", sa.String(16), nullable=False, server_default="active"),
            "subscription_due_at": sa.Column("subscription_due_at", sa.DateTime(), nullable=True),
            "suspended_reason": sa.Column("suspended_reason", sa.String(255), nullable=True),
            "webhook_set": sa.Column("webhook_set", sa.Boolean(), nullable=False, server_default="0"),
            "updated_at": sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        }
        for name, col in new_cols.items():
            if name not in cols:
                with op.batch_alter_table("tenants") as batch:
                    batch.add_column(col)
        # فهرس فريد على token_hash (حيث مدعوم)
        try:
            with op.batch_alter_table("tenants") as batch:
                batch.create_unique_constraint("uq_tenants_token_hash", ["token_hash"])
        except Exception:
            pass
    if "tenant_wallets" not in existing:
        op.create_table(
            "tenant_wallets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), unique=True, index=True),
            sa.Column("balance", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("total_funded_usd", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("total_spent_usd", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("total_earned_usd", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )
    if "tenant_category_margins" not in existing:
        op.create_table(
            "tenant_category_margins",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), index=True),
            sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id"), index=True),
            sa.Column("margin_percent", sa.Numeric(18, 4), nullable=False),
            sa.UniqueConstraint("tenant_id", "category_id", name="uq_tenant_category"),
        )
    if "tenant_catalog_selection" not in existing:
        op.create_table(
            "tenant_catalog_selection",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), index=True),
            sa.Column("item_type", sa.String(16), index=True),
            sa.Column("item_id", sa.Integer(), index=True),
            sa.UniqueConstraint("tenant_id", "item_type", "item_id", name="uq_tenant_selection"),
        )
    if "tenant_orders_map" not in existing:
        op.create_table(
            "tenant_orders_map",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), index=True),
            sa.Column("sub_order_type", sa.String(16), index=True),
            sa.Column("sub_order_id", sa.Integer(), index=True),
            sa.Column("main_order_id", sa.Integer(), index=True),
            sa.Column("base_price_usd", sa.Numeric(18, 4), nullable=False),
            sa.Column("fee_usd", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "sub_order_type", "sub_order_id", name="uq_tenant_sub_order"),
        )


def upgrade() -> None:
    _create_tenant_tables()
    bind = op.get_bind()
    insp = sa.inspect(bind)

    for table in TENANT_TABLES:
        if not _table_exists(insp, table):
            continue
        if "tenant_id" not in _column_names(insp, table):
            with op.batch_alter_table(table) as batch:
                batch.add_column(
                    sa.Column("tenant_id", sa.Integer(), nullable=False, server_default="0")
                )

    # ── users: فكّ القيد العام ← قيد مركب ──
    try:
        insp = sa.inspect(bind)
        if _table_exists(insp, "users"):
            existing_uniques = set()
            try:
                for uc in insp.get_unique_constraints("users"):
                    cols = tuple(uc.get("column_names") or [])
                    if set(cols) == {"tenant_id", "telegram_id"}:
                        existing_uniques.add("composite")
                    if set(cols) == {"telegram_id"} and uc.get("name"):
                        # إسقاط القيد العام المسمى (Postgres)
                        with op.batch_alter_table("users") as batch:
                            batch.drop_constraint(uc["name"], type_="unique")
            except Exception:
                pass
            try:
                # SQLite: الفريد العام فهرس (ix_users_telegram_id) لا قيد —
                # يُسقط كفهرس.
                for idx in insp.get_indexes("users"):
                    cols = list(idx.get("column_names") or [])
                    if cols == ["telegram_id"] and idx.get("unique"):
                        if idx.get("name") == "uq_user_tenant_telegram":
                            continue
                        with op.batch_alter_table("users") as batch:
                            try:
                                batch.drop_index(idx["name"])
                            except Exception:
                                pass
            except Exception:
                pass
            if "composite" not in existing_uniques:
                with op.batch_alter_table("users") as batch:
                    try:
                        batch.create_unique_constraint(
                            "uq_user_tenant_telegram", ["tenant_id", "telegram_id"]
                        )
                    except Exception:
                        pass
    except Exception:
        pass


def downgrade() -> None:
    for table in (
        "tenant_orders_map",
        "tenant_catalog_selection",
        "tenant_category_margins",
        "tenant_wallets",
        "tenants",
    ):
        try:
            op.drop_table(table)
        except Exception:
            pass
    # أعمدة tenant_id تُترك (إسقاطها يكسر البيانات) — الـ downgrade يزيل الجداول فقط.
