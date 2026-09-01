"""
إحصائيات البوت الشاملة.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select, func

from database.models import (
    User,
    DepositRequest,
    DepositStatus,
    NumberOrder,
    UnifiedOrder,
    OrderStatus,
    UnifiedOrderStatus,
    SupportTicket,
    SupportTicketStatus,
    ProductRequest,
    ProductRequestStatus,
)
from keyboards.admin import admin_back_kb
from filters.admin_filter import IsAdmin

router = Router(name="admin_stats")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:stats")
async def stats_handler(callback: CallbackQuery, session):
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    month_start = today_start - timedelta(days=30)

    # ── المستخدمون ──
    total_users = (await session.execute(select(func.count(User.id)))).scalar_one()

    new_users_today = (
        await session.execute(select(func.count(User.id)).where(User.joined_at >= today_start))
    ).scalar_one()

    new_users_week = (
        await session.execute(select(func.count(User.id)).where(User.joined_at >= week_start))
    ).scalar_one()

    banned_users = (
        await session.execute(select(func.count(User.id)).where(User.is_banned.is_(True)))
    ).scalar_one()

    # ── الإيداعات ──
    today_deposits = (
        await session.execute(
            select(func.coalesce(func.sum(DepositRequest.amount_usd), 0)).where(
                DepositRequest.status == DepositStatus.APPROVED,
                DepositRequest.processed_at >= today_start,
            )
        )
    ).scalar_one()
    today_deposits = Decimal(str(today_deposits))

    week_deposits = (
        await session.execute(
            select(func.coalesce(func.sum(DepositRequest.amount_usd), 0)).where(
                DepositRequest.status == DepositStatus.APPROVED,
                DepositRequest.processed_at >= week_start,
            )
        )
    ).scalar_one()
    week_deposits = Decimal(str(week_deposits))

    month_deposits = (
        await session.execute(
            select(func.coalesce(func.sum(DepositRequest.amount_usd), 0)).where(
                DepositRequest.status == DepositStatus.APPROVED,
                DepositRequest.processed_at >= month_start,
            )
        )
    ).scalar_one()
    month_deposits = Decimal(str(month_deposits))

    pending_deposits = (
        await session.execute(
            select(func.count(DepositRequest.id)).where(
                DepositRequest.status == DepositStatus.PENDING
            )
        )
    ).scalar_one()

    open_tickets = (
        await session.execute(
            select(func.count(SupportTicket.id)).where(
                SupportTicket.status.in_(
                    [SupportTicketStatus.OPEN, SupportTicketStatus.IN_PROGRESS]
                )
            )
        )
    ).scalar_one()

    market_requests = (
        await session.execute(
            select(func.count(ProductRequest.id)).where(
                ProductRequest.status.in_(
                    [ProductRequestStatus.OPEN, ProductRequestStatus.IN_REVIEW]
                )
            )
        )
    ).scalar_one()

    # ── طلبات الأرقام ──
    today_num_sales = (
        await session.execute(
            select(func.count(NumberOrder.id)).where(
                NumberOrder.status == OrderStatus.COMPLETED,
                NumberOrder.purchased_at >= today_start,
            )
        )
    ).scalar_one()

    total_num_sales = (
        await session.execute(
            select(func.count(NumberOrder.id)).where(NumberOrder.status == OrderStatus.COMPLETED)
        )
    ).scalar_one()

    num_revenue = (
        await session.execute(
            select(func.coalesce(func.sum(NumberOrder.price_sell_usd), 0)).where(
                NumberOrder.status == OrderStatus.COMPLETED,
                NumberOrder.purchased_at >= month_start,
            )
        )
    ).scalar_one()
    num_revenue = Decimal(str(num_revenue))

    num_cost = (
        await session.execute(
            select(func.coalesce(func.sum(NumberOrder.price_provider_usd), 0)).where(
                NumberOrder.status == OrderStatus.COMPLETED,
                NumberOrder.purchased_at >= month_start,
            )
        )
    ).scalar_one()
    num_cost = Decimal(str(num_cost))

    # ── طلبات موحدة (ألعاب/تطبيقات/SMM) ──
    today_uni_sales = (
        await session.execute(
            select(func.count(UnifiedOrder.id)).where(
                UnifiedOrder.status == UnifiedOrderStatus.COMPLETED,
                UnifiedOrder.created_at >= today_start,
            )
        )
    ).scalar_one()

    total_uni_sales = (
        await session.execute(
            select(func.count(UnifiedOrder.id)).where(
                UnifiedOrder.status == UnifiedOrderStatus.COMPLETED
            )
        )
    ).scalar_one()

    uni_revenue = (
        await session.execute(
            select(func.coalesce(func.sum(UnifiedOrder.price_usd), 0)).where(
                UnifiedOrder.status == UnifiedOrderStatus.COMPLETED,
                UnifiedOrder.created_at >= month_start,
            )
        )
    ).scalar_one()
    uni_revenue = Decimal(str(uni_revenue))

    uni_cost = (
        await session.execute(
            select(func.coalesce(func.sum(UnifiedOrder.cost_price_usd), 0)).where(
                UnifiedOrder.status == UnifiedOrderStatus.COMPLETED,
                UnifiedOrder.created_at >= month_start,
            )
        )
    ).scalar_one()
    uni_cost = Decimal(str(uni_cost))

    # ── الأرباح الصافية ──
    total_revenue = num_revenue + uni_revenue
    total_cost = num_cost + uni_cost
    net_profit = total_revenue - total_cost

    # ── إجمالي أرصدة المستخدمين ──
    total_balances = (
        await session.execute(select(func.coalesce(func.sum(User.balance), 0)))
    ).scalar_one()
    total_balances = Decimal(str(total_balances))

    await callback.message.edit_text(
        "📊 <b>إحصائيات البوت</b>\n\n"
        "━━━ 👥 المستخدمون ━━━\n"
        f"الإجمالي: {total_users}\n"
        f"جدد اليوم: {new_users_today}\n"
        f"جدد هذا الأسبوع: {new_users_week}\n"
        f"محظورون: {banned_users}\n\n"
        "━━━ 💰 الإيداعات ━━━\n"
        f"اليوم: {today_deposits:.2f}$\n"
        f"الأسبوع: {week_deposits:.2f}$\n"
        f"الشهر: {month_deposits:.2f}$\n"
        f"⏳ إيداعات معلّقة: {pending_deposits}\n"
        f"🎫 تذاكر مفتوحة: {open_tickets}\n"
        f"📈 طلبات سوق قيد الدراسة: {market_requests}\n\n"
        "━━━ 📞 طلبات الأرقام ━━━\n"
        f"اليوم: {today_num_sales}\n"
        f"الإجمالي: {total_num_sales}\n\n"
        "━━━ 🛒 طلبات أخرى (ألعاب/SMM) ━━━\n"
        f"اليوم: {today_uni_sales}\n"
        f"الإجمالي: {total_uni_sales}\n\n"
        "━━━ 💵 الأرباح (آخر 30 يوم) ━━━\n"
        f"إجمالي المبيعات: {total_revenue:.2f}$\n"
        f"إجمالي التكاليف: {total_cost:.2f}$\n"
        f"صافي الأرباح: <b>{net_profit:.2f}$</b>\n\n"
        "━━━ 💳 أرصدة المستخدمين ━━━\n"
        f"إجمالي الأرصدة: {total_balances:.2f}$",
        reply_markup=admin_back_kb(),
    )
