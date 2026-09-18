"""
ملف المستخدم المفصل — بطاقة حساب تفاعلية بمعلومات الإنفاق والطلبات.

يحسب الإحصاءات الحية من جداول الطلبات الحقيقية: أكثر خدمة،
أكثر دولة، عدد الطلبات النشطة، آخر شراء، والإنفاق الشهري.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from database.models import NumberOrder, SpecialOfferOrder, User
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class UserProfileService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("enhanced_user_profile")

    @staticmethod
    async def build_profile(session, user_id: int) -> dict:
        user = await session.get(User, user_id)
        if user is None:
            raise ValueError("المستخدم غير موجود")

        current_month_start = datetime.utcnow().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        number_rows = await session.execute(
            select(NumberOrder).where(NumberOrder.user_id == user_id)
        )
        number_orders = list(number_rows.scalars().all())

        offer_rows = await session.execute(
            select(SpecialOfferOrder).where(SpecialOfferOrder.user_id == user_id)
        )
        offer_orders = list(offer_rows.scalars().all())

        total_orders = len(number_orders) + len(offer_orders)
        total_spend = sum((o.price_sell_usd for o in number_orders), Decimal("0"))
        total_spend += sum((o.price_usd for o in offer_orders), Decimal("0"))

        month_spend = sum(
            (o.price_sell_usd for o in number_orders if o.purchased_at >= current_month_start),
            Decimal("0"),
        )
        month_spend += sum(
            (
                o.price_usd
                for o in offer_orders
                if o.created_at >= current_month_start
            ),
            Decimal("0"),
        )
        month_orders = sum(
            1 for o in number_orders if o.purchased_at >= current_month_start
        ) + sum(1 for o in offer_orders if o.created_at >= current_month_start)

        active = [o for o in number_orders if o.status.value in ("pending", "processing")]
        services = Counter(o.service for o in number_orders)
        countries = Counter(o.country_code for o in number_orders)

        last_order = None
        candidates = []
        if number_orders:
            candidates.append(max(number_orders, key=lambda o: o.purchased_at))
        if offer_orders:
            candidates.append(max(offer_orders, key=lambda o: o.created_at))
        if candidates:
            last_order = max(candidates, key=lambda o: getattr(o, "purchased_at", None) or getattr(o, "created_at", None))

        last_activity = user.last_activity_at
        if last_order is not None:
            order_time = getattr(last_order, "purchased_at", None) or getattr(last_order, "created_at", None)
            if last_activity is None or (order_time and order_time > last_activity):
                last_activity = order_time

        # عدد الإحالات باستعلام عدّ صريح — الوصول لعلاقة user.referrals
        # داخل AsyncSession يرفع MissingGreenlet (تحميل كسول غير مدعوم).
        referral_count = int(
            (
                await session.execute(
                    select(func.count(User.id)).where(User.referrer_id == user_id)
                )
            ).scalar_one()
        )

        return {
            "user": user,
            "total_orders": total_orders,
            "total_spend_usd": total_spend,
            "month_orders": month_orders,
            "month_spend_usd": month_spend,
            "active_orders": len(active),
            "top_service": services.most_common(1)[0] if services else None,
            "top_country": countries.most_common(1)[0] if countries else None,
            "loyalty_points": user.loyalty_points or 0,
            "loyalty_tier_name": None,
            "display_currency": user.display_currency,
            "last_activity_at": last_activity,
            "joined_at": user.joined_at,
            "balance": user.balance,
            "referral_count": referral_count,
        }

    @staticmethod
    async def render_profile(session, user_id: int) -> str:
        data = await UserProfileService.build_profile(session, user_id)
        user = data["user"]
        name = user.full_name or user.username or f"مستخدم {user.id}"
        handle = f" @{user.username}" if user.username else ""
        tier = data["loyalty_tier_name"]
        try:
            from services.loyalty_service import LoyaltyService

            tier = LoyaltyService.tier_for_points(data["loyalty_points"]).name
        except Exception:
            tier = "Bronze"

        lines = [
            f"👤 <b>ملفك الشخصي</b>\n",
            f"الاسم: {name}{handle}",
            f"الرصيد الحالي: <b>{data['balance']:g}$</b>",
            f"نقاط الولاء: <b>{data['loyalty_points']}</b> ({tier})",
            "",
            "📈 <b>الملخص</b>",
            f"إجمالي الطلبات: {data['total_orders']}",
            f"إجمالي الإنفاق: {data['total_spend_usd']:g}$",
            f"طلبات هذا الشهر: {data['month_orders']} ({data['month_spend_usd']:g}$)",
            f"طلبات قيد التنفيذ: {data['active_orders']}",
        ]
        if data["top_service"]:
            lines.append(f"🛍 أكثر خدمة: {data['top_service'][0]}")
        if data["top_country"]:
            lines.append(f"🌍 أكثر دولة: {data['top_country'][0]}")
        if data["referral_count"]:
            lines.append(f"👥 أعضاء دعوتك: {data['referral_count']}")
        if data["last_activity_at"]:
            lines.append(f"\n🕒 آخر نشاط: {data['last_activity_at'].strftime('%Y-%m-%d %H:%M')} UTC")
        if data["joined_at"]:
            lines.append(f"📅 تاريخ الانضمام: {data['joined_at'].strftime('%Y-%m-%d')}")
        return "\n".join(lines)