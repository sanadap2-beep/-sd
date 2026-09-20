"""Safely migrate a legacy RUB database to the current USD schema.

The old version of this script was unsafe: it always divided live balances by
30, ignored DATABASE_URL, and referenced number-order columns that do not
exist in the current model. This version refuses to change money unless the
operator explicitly passes ``--from-rub``.

Before running against a real database:

    cp bot_database.db bot_database_backup.db
    python migrate_to_usd.py --from-rub --rate 30

Stop the bot while migrating. A fresh database does not need this script;
``database.seed.init_db`` creates the current USD schema automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot_database.db")
engine = create_async_engine(DATABASE_URL, echo=False)


def rub_to_usd(amount_rub: Decimal, rate: Decimal) -> Decimal:
    if rate <= 0:
        raise ValueError("سعر الصرف يجب أن يكون أكبر من صفر")
    return (amount_rub / rate).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


async def _tables(connection: AsyncConnection) -> set[str]:
    if connection.dialect.name == "sqlite":
        result = await connection.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table'")
        )
    else:
        # The bot officially uses SQLite. Keep a useful error for unsupported
        # URLs instead of silently running a SQLite-specific migration.
        raise RuntimeError("هذا السكربت يدعم SQLite فقط؛ استخدم migration مناسبة لقاعدة أخرى.")
    return {row[0] for row in result.fetchall()}


async def _columns(connection: AsyncConnection, table: str) -> set[str]:
    result = await connection.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result.fetchall()}


async def _add_column(
    connection: AsyncConnection,
    table: str,
    columns: set[str],
    name: str,
    definition: str,
) -> None:
    if name not in columns:
        await connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
        columns.add(name)
        logger.info("تمت إضافة %s.%s", table, name)


def _decimal(value: object) -> Decimal:
    try:
        amount = Decimal(str(value if value is not None else "0"))
    except (InvalidOperation, ValueError):
        raise ValueError(f"قيمة مالية غير صالحة: {value!r}") from None
    if not amount.is_finite():
        raise ValueError(f"قيمة مالية غير صالحة: {value!r}")
    return amount


async def _convert_column(
    connection: AsyncConnection,
    table: str,
    id_column: str,
    amount_columns: tuple[str, ...],
    rate: Decimal,
) -> int:
    columns = ", ".join((id_column, *amount_columns))
    result = await connection.execute(text(f"SELECT {columns} FROM {table}"))
    rows = result.fetchall()
    changed = 0

    for row in rows:
        converted = [rub_to_usd(_decimal(value), rate) for value in row[1:]]
        assignments = ", ".join(
            f"{column} = :value_{index}" for index, column in enumerate(amount_columns)
        )
        params = {f"value_{index}": str(value) for index, value in enumerate(converted)}
        params["row_id"] = row[0]
        await connection.execute(
            text(f"UPDATE {table} SET {assignments} WHERE {id_column} = :row_id"),
            params,
        )
        changed += 1
    return changed


async def migrate(rate: Decimal, from_rub: bool) -> None:
    if rate <= 0:
        raise ValueError("سعر الصرف يجب أن يكون أكبر من صفر")

    async with engine.begin() as connection:
        tables = await _tables(connection)
        if "users" not in tables:
            logger.info("لا توجد قاعدة بيانات قديمة للترحيل.")
            return

        if not from_rub:
            logger.warning(
                "لم يتم تعديل أي مبلغ. إذا كانت القاعدة القديمة بالروبل، "
                "أعد التشغيل مع --from-rub --rate RATE."
            )
            return

        logger.warning(
            "سيتم تحويل الأرصدة الحالية من RUB إلى USD بسعر 1 USD = %s RUB.",
            rate,
        )

        users_columns = await _columns(connection, "users")
        if "balance" in users_columns:
            count = await _convert_column(connection, "users", "id", ("balance",), rate)
            logger.info("تم تحويل أرصدة %s مستخدم", count)

        if "transactions" in tables:
            tx_columns = await _columns(connection, "transactions")
            if {"amount", "balance_after"} <= tx_columns:
                count = await _convert_column(
                    connection,
                    "transactions",
                    "id",
                    ("amount", "balance_after"),
                    rate,
                )
                logger.info("تم تحويل %s معاملة", count)

        if "deposit_requests" in tables:
            deposit_columns = await _columns(connection, "deposit_requests")
            if "amount_rub" in deposit_columns:
                await _add_column(
                    connection,
                    "deposit_requests",
                    deposit_columns,
                    "amount_usd",
                    "NUMERIC(18,4)",
                )
                result = await connection.execute(
                    text("SELECT id, amount_rub FROM deposit_requests")
                )
                rows = result.fetchall()
                for deposit_id, amount_rub in rows:
                    amount_usd = rub_to_usd(_decimal(amount_rub), rate)
                    await connection.execute(
                        text(
                            "UPDATE deposit_requests "
                            "SET amount_usd = :amount_usd "
                            "WHERE id = :deposit_id"
                        ),
                        {
                            "amount_usd": str(amount_usd),
                            "deposit_id": deposit_id,
                        },
                    )
                logger.info("تم تحويل %s طلب إيداع", len(rows))

        if "number_orders" in tables:
            order_columns = await _columns(connection, "number_orders")
            legacy_pairs = (
                ("price_provider_rub", "price_provider_usd"),
                ("price_sell_rub", "price_sell_usd"),
            )
            for legacy, current in legacy_pairs:
                if legacy in order_columns:
                    await _add_column(
                        connection,
                        "number_orders",
                        order_columns,
                        current,
                        "NUMERIC(18,4)",
                    )
                    result = await connection.execute(
                        text(f"SELECT id, {legacy} FROM number_orders")
                    )
                    for order_id, legacy_value in result.fetchall():
                        current_value = rub_to_usd(_decimal(legacy_value), rate)
                        await connection.execute(
                            text(
                                f"UPDATE number_orders SET {current} = "
                                ":current_value WHERE id = :order_id"
                            ),
                            {
                                "current_value": str(current_value),
                                "order_id": order_id,
                            },
                        )
            logger.info("تم ترحيل أسعار طلبات الأرقام القديمة إن وجدت")

        if "transfers" in tables:
            transfer_columns = await _columns(connection, "transfers")
            if "amount" in transfer_columns:
                count = await _convert_column(connection, "transfers", "id", ("amount",), rate)
                logger.info("تم تحويل %s تحويل", count)

        # Columns introduced after the first release. These additions are
        # safe for existing SQLite databases and are also handled by init_db.
        for table, name, definition in (
            ("users", "total_spent_usd", "NUMERIC(18,4) DEFAULT 0"),
            ("users", "total_orders", "INTEGER DEFAULT 0"),
            ("users", "cashback_earned_usd", "NUMERIC(18,4) DEFAULT 0"),
            ("deposit_requests", "reject_reason", "VARCHAR(255)"),
            ("countries", "sms_activate_code", "VARCHAR(16)"),
            ("countries", "smshub_code", "VARCHAR(16)"),
            ("countries", "smspool_code", "VARCHAR(32)"),
            ("countries", "grizzly_code", "VARCHAR(32)"),
            ("number_services", "smspool_code", "VARCHAR(32)"),
            ("number_services", "grizzly_code", "VARCHAR(32)"),
            ("countries", "sort_order", "INTEGER DEFAULT 0"),
        ):
            if table in tables:
                columns = await _columns(connection, table)
                await _add_column(connection, table, columns, name, definition)

        if "settings" in tables:
            await connection.execute(
                text("DELETE FROM settings WHERE key = 'exchange_rate_usd_rub'")
            )
            defaults = {
                "maintenance_mode": "false",
                "maintenance_message": "⚙️ البوت تحت الصيانة حالياً، سيعود قريباً...",
                "public_channel_id": "0",
                "backup_channel_id": "0",
                "stars_rate_usd": "0.013",
                "max_active_orders": "3",
                "cashback_percent": "0",
                "referral_percent": "5",
                "rate_limit_seconds": "30",
                "provider_low_balance_threshold": "10",
                "large_order_confirm_usd": "20",
                "min_deposit_shamcash_usd": "0.5",
                "min_deposit_usdt_usd": "2",
                "min_deposit_stars_usd": "1",
                "welcome_message": "👋 أهلاً بك في البوت!",
                "order_timeout_minutes": "5",
                "default_profit_margin_percent": "50",
                "referral_bonus_usd": "0.015",
                "require_subscription_for_referral": "true",
            }
            for key, value in defaults.items():
                await connection.execute(
                    text("INSERT OR IGNORE INTO settings (key, value) VALUES (:key, :value)"),
                    {"key": key, "value": value},
                )

    logger.info("✅ اكتمل الترحيل بنجاح. شغّل init_db عند تشغيل البوت.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ترحيل قاعدة بيانات SQLite القديمة من RUB إلى USD")
    parser.add_argument(
        "--from-rub",
        action="store_true",
        help="فعّل التحويل المالي صراحةً (مطلوب للحماية)",
    )
    parser.add_argument(
        "--rate",
        type=Decimal,
        default=Decimal("30"),
        help="سعر الصرف: عدد الروبل لكل دولار (الافتراضي 30)",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    try:
        await migrate(args.rate, args.from_rub)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
