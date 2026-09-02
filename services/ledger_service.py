"""User-only financial ledger for the admin panel.

Admin deposits and admin purchases are excluded so the books reflect
customers, not the owner testing the bot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from database.models import (
    NumberOrder,
    NumberService,
    OrderStatus,
    Product,
    Transaction,
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)

MONEY = Decimal("0.0001")
DEPOSITS_PER_PAGE = 8
SERVICES_PER_PAGE = 8

USER_DEPOSIT_TYPES = (TransactionType.DEPOSIT, TransactionType.STARS_DEPOSIT)
COUNTED_UNIFIED = (UnifiedOrderStatus.COMPLETED, UnifiedOrderStatus.PARTIAL)

PERIOD_LABELS = {
    "d": "اليوم",
    "w": "آخر 7 أيام",
    "m": "آخر 30 يوم",
    "all": "كل الفترة",
}


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def period_start(period: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.utcnow()
    if period == "d":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "w":
        return now - timedelta(days=7)
    if period == "m":
        return now - timedelta(days=30)
    return None


def normalize_period(raw: str | None) -> str:
    if raw in PERIOD_LABELS:
        return raw
    return "m"


@dataclass(frozen=True)
class LedgerSummary:
    period: str
    deposit_total: Decimal
    deposit_count: int
    sales_total: Decimal
    cost_total: Decimal
    profit_total: Decimal
    order_count: int
    customer_balances: Decimal
    customer_count: int


@dataclass
class DepositRow:
    user_name: str
    username: str | None
    telegram_id: int
    amount: Decimal
    created_at: datetime
    description: str | None
    tx_type: TransactionType


@dataclass
class ServiceProfitRow:
    name: str
    kind: str
    orders: int
    sales: Decimal
    cost: Decimal
    profit: Decimal


class LedgerService:
    """Accounting views restricted to non-admin customers."""

    @staticmethod
    def _customer_filter():
        return User.is_admin.is_(False)

    @staticmethod
    def _period_clause(column, period: str):
        start = period_start(period)
        if start is None:
            return None
        return column >= start

    @staticmethod
    def _where(*clauses):
        return [clause for clause in clauses if clause is not None]

    @staticmethod
    async def user_realized_totals(session, user_id: int) -> tuple[Decimal, int]:
        """إجمالي مشتريات المستخدم المحقَّقة فعلاً + عددها.

        تحتسب فقط ما اكتمل وتفعّل:
        - الأرقام: بعد وصول الكود وتفعيل الرقم (COMPLETED).
        - طلبات الرشق/الألعاب/التطبيقات: عند الاكتمال (COMPLETED/PARTIAL).

        لا تحتسب الطلبات المعلّقة أو التي فشلت/استُرجعت، لأن المشتري قد
        دفع مسبقاً لكن جزءاً كبيراً منها لا يتفعّل ويُرجع رصيده لاحقاً.
        هذا هو المعروض للمستخدم في «إجمالي مشترياتك» ببطاقة الحساب.
        """
        uni_q = (
            select(
                func.coalesce(func.sum(UnifiedOrder.price_usd), 0),
                func.count(UnifiedOrder.id),
            ).where(
                UnifiedOrder.user_id == user_id,
                UnifiedOrder.status.in_(
                    [UnifiedOrderStatus.COMPLETED, UnifiedOrderStatus.PARTIAL]
                ),
            )
        )
        uni_sum, uni_count = (await session.execute(uni_q)).one()

        num_q = (
            select(
                func.coalesce(func.sum(NumberOrder.price_sell_usd), 0),
                func.count(NumberOrder.id),
            ).where(
                NumberOrder.user_id == user_id,
                NumberOrder.status == OrderStatus.COMPLETED,
            )
        )
        num_sum, num_count = (await session.execute(num_q)).one()

        total = money(uni_sum) + money(num_sum)
        return total, int(uni_count or 0) + int(num_count or 0)

    @staticmethod
    async def summary(session, period: str = "m") -> LedgerSummary:
        period = normalize_period(period)
        customers = LedgerService._customer_filter()

        deposit_q = (
            select(
                func.coalesce(func.sum(Transaction.amount), 0),
                func.count(Transaction.id),
            )
            .join(User, User.id == Transaction.user_id)
            .where(
                *LedgerService._where(
                    customers,
                    Transaction.type.in_(USER_DEPOSIT_TYPES),
                    LedgerService._period_clause(Transaction.created_at, period),
                )
            )
        )
        deposit_total, deposit_count = (await session.execute(deposit_q)).one()

        uni_q = (
            select(
                func.coalesce(func.sum(UnifiedOrder.price_usd), 0),
                func.coalesce(func.sum(UnifiedOrder.cost_price_usd), 0),
                func.count(UnifiedOrder.id),
            )
            .join(User, User.id == UnifiedOrder.user_id)
            .where(
                *LedgerService._where(
                    customers,
                    UnifiedOrder.status.in_(COUNTED_UNIFIED),
                    UnifiedOrder.quantity > 0,
                    LedgerService._period_clause(UnifiedOrder.created_at, period),
                )
            )
        )
        uni_sales, uni_cost, uni_orders = (await session.execute(uni_q)).one()

        num_q = (
            select(
                func.coalesce(func.sum(NumberOrder.price_sell_usd), 0),
                func.coalesce(func.sum(NumberOrder.price_provider_usd), 0),
                func.count(NumberOrder.id),
            )
            .join(User, User.id == NumberOrder.user_id)
            .where(
                *LedgerService._where(
                    customers,
                    NumberOrder.status == OrderStatus.COMPLETED,
                    LedgerService._period_clause(NumberOrder.purchased_at, period),
                )
            )
        )
        num_sales, num_cost, num_orders = (await session.execute(num_q)).one()

        bal_q = select(
            func.coalesce(func.sum(User.balance), 0),
            func.count(User.id),
        ).where(customers)
        balances, customer_count = (await session.execute(bal_q)).one()

        sales = money(uni_sales) + money(num_sales)
        cost = money(uni_cost) + money(num_cost)
        return LedgerSummary(
            period=period,
            deposit_total=money(deposit_total),
            deposit_count=int(deposit_count or 0),
            sales_total=sales,
            cost_total=cost,
            profit_total=sales - cost,
            order_count=int(uni_orders or 0) + int(num_orders or 0),
            customer_balances=money(balances),
            customer_count=int(customer_count or 0),
        )

    @staticmethod
    async def list_deposits(
        session,
        period: str = "m",
        page: int = 0,
        per_page: int = DEPOSITS_PER_PAGE,
    ) -> tuple[list[DepositRow], int]:
        period = normalize_period(period)
        page = max(0, page)
        filters = LedgerService._where(
            LedgerService._customer_filter(),
            Transaction.type.in_(USER_DEPOSIT_TYPES),
            LedgerService._period_clause(Transaction.created_at, period),
        )
        total = (
            await session.execute(
                select(func.count(Transaction.id))
                .join(User, User.id == Transaction.user_id)
                .where(*filters)
            )
        ).scalar_one()
        result = await session.execute(
            select(Transaction)
            .options(selectinload(Transaction.user))
            .join(User, User.id == Transaction.user_id)
            .where(*filters)
            .order_by(Transaction.created_at.desc(), Transaction.id.desc())
            .limit(per_page)
            .offset(page * per_page)
        )
        rows = []
        for tx in result.scalars().all():
            user = tx.user
            rows.append(
                DepositRow(
                    user_name=(user.full_name if user else None) or "مستخدم",
                    username=user.username if user else None,
                    telegram_id=user.telegram_id if user else 0,
                    amount=money(tx.amount),
                    created_at=tx.created_at,
                    description=tx.description,
                    tx_type=tx.type,
                )
            )
        return rows, int(total or 0)

    @staticmethod
    async def service_profits(
        session,
        period: str = "m",
        page: int = 0,
        per_page: int = SERVICES_PER_PAGE,
    ) -> tuple[list[ServiceProfitRow], int, Decimal, Decimal, Decimal]:
        period = normalize_period(period)
        page = max(0, page)
        customers = LedgerService._customer_filter()

        uni_rows = (
            await session.execute(
                select(
                    Product.name_ar,
                    func.count(UnifiedOrder.id),
                    func.coalesce(func.sum(UnifiedOrder.price_usd), 0),
                    func.coalesce(func.sum(UnifiedOrder.cost_price_usd), 0),
                )
                .join(User, User.id == UnifiedOrder.user_id)
                .join(Product, Product.id == UnifiedOrder.product_id)
                .where(
                    *LedgerService._where(
                        customers,
                        UnifiedOrder.status.in_(COUNTED_UNIFIED),
                        UnifiedOrder.quantity > 0,
                        LedgerService._period_clause(UnifiedOrder.created_at, period),
                    )
                )
                .group_by(Product.name_ar)
            )
        ).all()

        num_rows = (
            await session.execute(
                select(
                    NumberOrder.service,
                    func.count(NumberOrder.id),
                    func.coalesce(func.sum(NumberOrder.price_sell_usd), 0),
                    func.coalesce(func.sum(NumberOrder.price_provider_usd), 0),
                )
                .join(User, User.id == NumberOrder.user_id)
                .where(
                    *LedgerService._where(
                        customers,
                        NumberOrder.status == OrderStatus.COMPLETED,
                        LedgerService._period_clause(NumberOrder.purchased_at, period),
                    )
                )
                .group_by(NumberOrder.service)
            )
        ).all()

        number_names = {
            code: (emoji, name)
            for code, emoji, name in (
                await session.execute(
                    select(NumberService.code, NumberService.emoji, NumberService.name_ar)
                )
            ).all()
        }

        items: list[ServiceProfitRow] = []
        for name, count, sales, cost in uni_rows:
            sales_m, cost_m = money(sales), money(cost)
            items.append(
                ServiceProfitRow(
                    name=name or "منتج",
                    kind="product",
                    orders=int(count or 0),
                    sales=sales_m,
                    cost=cost_m,
                    profit=sales_m - cost_m,
                )
            )
        for code, count, sales, cost in num_rows:
            emoji, label = number_names.get(code, ("📞", code or "رقم"))
            sales_m, cost_m = money(sales), money(cost)
            items.append(
                ServiceProfitRow(
                    name=f"{emoji} {label}".strip(),
                    kind="number",
                    orders=int(count or 0),
                    sales=sales_m,
                    cost=cost_m,
                    profit=sales_m - cost_m,
                )
            )

        items.sort(key=lambda row: (row.profit, row.sales, row.orders), reverse=True)
        total_sales = sum((row.sales for row in items), Decimal("0"))
        total_cost = sum((row.cost for row in items), Decimal("0"))
        total_profit = total_sales - total_cost
        start = page * per_page
        return items[start : start + per_page], len(items), money(total_sales), money(total_cost), money(total_profit)
