"""Run Alembic migrations safely during startup.

The application historically treated any non-empty database without an
``alembic_version`` table as if it were already at the latest revision. That
is unsafe: an old database can have the same table names while still missing
columns introduced by later migrations. This module only auto-recovers a
known initial schema; unknown unversioned databases fail closed with a useful
operator-facing error.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from config import settings


_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)

# The first Alembic revision was generated from this set of tables. It is the
# only unversioned legacy shape that we can identify without guessing which
# later migrations have already been applied.
_INITIAL_SCHEMA_REVISION = "403e80d4c7c9"
_INITIAL_SCHEMA_TABLES = frozenset(
    {
        "api_providers",
        "categories",
        "challenges",
        "mandatory_channels",
        "number_services",
        "provider_status",
        "service_pricing",
        "settings",
        "stars_packages",
        "users",
        "abuse_events",
        "audit_logs",
        "auto_invoices",
        "broadcast_logs",
        "cashback_logs",
        "challenge_progress",
        "countries",
        "coupons",
        "deposit_requests",
        "gift_codes",
        "loyalty_events",
        "number_orders",
        "product_requests",
        "provider_services",
        "rate_limit_logs",
        "reseller_accounts",
        "sub_categories",
        "support_tickets",
        "transactions",
        "transfers",
        "coupon_usages",
        "gift_redemptions",
        "product_request_votes",
        "products",
        "reseller_api_keys",
        "product_watches",
        "promotions",
        "user_favorites",
        "unified_orders",
        "digital_inventory_items",
        "product_reviews",
    }
)

# If any of these tables already exists, the database may contain part of a
# later migration. Guessing the baseline in that situation could skip schema
# changes, so the operator must repair or explicitly baseline it.
_POST_INITIAL_MARKERS = frozenset(
    {
        "cart_items",
        "feature_flags",
        "feature_events",
        "tasks_catalog",
        "market_listings",
        "market_transactions",
        "unified_refunds",
        "tenants",
    }
)


def _sync_url() -> str:
    return settings.DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", "")


def _unversioned_schema_action(tables: set[str]) -> str:
    """Return the safe action for a database without ``alembic_version``.

    This small pure helper also makes the fail-closed policy easy to test
    without touching a real database.
    """
    if not tables:
        return "upgrade"
    if _INITIAL_SCHEMA_TABLES.issubset(tables) and not tables.intersection(
        _POST_INITIAL_MARKERS
    ):
        return "baseline_initial"
    return "reject"


def _legacy_schema_error(tables: set[str]) -> RuntimeError:
    missing = sorted(_INITIAL_SCHEMA_TABLES - tables)
    markers = sorted(tables.intersection(_POST_INITIAL_MARKERS))
    details = []
    if missing:
        details.append(f"missing initial tables: {', '.join(missing[:8])}")
    if markers:
        details.append(f"post-initial tables present: {', '.join(markers)}")
    detail_text = "; ".join(details) or "schema shape is not recognized"
    return RuntimeError(
        "Database contains tables but no alembic_version; refusing to stamp head "
        f"automatically ({detail_text}). Back up the database, identify its "
        "schema revision, and run an explicit repair/baseline before starting "
        "the bot."
    )


def _run_sync() -> None:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", _sync_url().replace("%", "%%"))
    engine = create_engine(_sync_url(), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())

        if tables and "alembic_version" not in tables:
            action = _unversioned_schema_action(tables)
            if action == "reject":
                raise _legacy_schema_error(tables)

            # This is a known pre-Alembic/initial-revision shape. Mark it at
            # the initial revision, then let Alembic apply every later change.
            # Never mark an unknown schema as head.
            logger.warning(
                "Unversioned database matches the initial schema; baselining at %s "
                "before applying remaining migrations.",
                _INITIAL_SCHEMA_REVISION,
            )
            command.stamp(config, _INITIAL_SCHEMA_REVISION)

        command.upgrade(config, "head")
    finally:
        engine.dispose()


async def run_migrations() -> None:
    await asyncio.to_thread(_run_sync)
