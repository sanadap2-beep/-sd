"""لوحة إحصائيات برنامج الولاء."""

from datetime import datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import desc, func, select

from database.models import LoyaltyEvent, User
from filters.admin_filter import IsAdmin
from keyboards.admin import admin_back_kb
from services.loyalty_service import TIERS
from services.settings_service import SettingsService

router = Router(name="admin_loyalty")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:loyalty")
async def loyalty_dashboard(callback: CallbackQuery, session):
    total_points = (
        await session.execute(select(func.coalesce(func.sum(User.loyalty_points), 0)))
    ).scalar_one()
    participants = (
        await session.execute(select(func.count(User.id)).where(User.loyalty_points > 0))
    ).scalar_one()
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_events = (
        await session.execute(
            select(func.count(LoyaltyEvent.id)).where(
                LoyaltyEvent.created_at >= today,
                LoyaltyEvent.event_type == "daily_checkin",
            )
        )
    ).scalar_one()
    redeemed = (
        await session.execute(
            select(func.coalesce(func.sum(-LoyaltyEvent.points), 0)).where(
                LoyaltyEvent.event_type == "redeem"
            )
        )
    ).scalar_one()
    top_result = await session.execute(
        select(User).where(User.loyalty_points > 0).order_by(desc(User.loyalty_points)).limit(5)
    )
    top_users = list(top_result.scalars().all())

    points_per_usd = await SettingsService.get_decimal("loyalty_points_per_usd", Decimal("10"))
    daily_points = await SettingsService.get_int("loyalty_daily_points", 25)
    tiers = "\n".join(
        f"{tier.emoji} {tier.name}: {tier.minimum_points} نقطة (x{tier.points_multiplier})"
        for tier in TIERS
    )
    top_text = (
        "\n".join(
            f"{index}. {user.full_name or user.telegram_id}: {user.loyalty_points} نقطة"
            for index, user in enumerate(top_users, 1)
        )
        or "لا يوجد مستخدمون بعد."
    )

    text = (
        "🎁 <b>برنامج الولاء</b>\n\n"
        f"💎 مجموع النقاط المتداولة: <b>{total_points}</b>\n"
        f"👥 المشاركون: <b>{participants}</b>\n"
        f"🎉 تسجيلات اليوم: <b>{today_events}</b>\n"
        f"💸 النقاط المستبدلة: <b>{redeemed}</b>\n\n"
        "━━━ ⚙️ الإعدادات الحالية ━━━\n"
        f"نقاط كل دولار: {points_per_usd}\n"
        f"مكافأة التسجيل اليومي: {daily_points}\n\n"
        "━━━ 🏆 المتصدرون ━━━\n"
        f"{top_text}\n\n"
        "━━━ 🪜 المستويات ━━━\n"
        f"{tiers}"
    )
    await callback.message.edit_text(text, reply_markup=admin_back_kb())
    await callback.answer()
