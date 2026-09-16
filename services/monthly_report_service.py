"""
التقرير الشهري الشخصي.

في أول كل شهر يُجمع ملخص إنفاق وطلبات المستخدم من جداول الطلبات
الواقعية وتُرسل بطاقة HTML مقروءة. يزود البوت البيانات فقط —
الإرسال من المعالج النصي للمستخدم بما يقتضي الرسالة الموجودة.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from database.models import LoyaltyEvent, NumberOrder, SpecialOfferOrder, User
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class MonthlyReportService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("monthly_report")

    @staticmethod
    async def _month_bounds() -> tuple[datetime, datetime, str, str]:
        """بدأ ونهاية الشهر السابق المكتمل + تسمية مقروءة."""
        now = datetime.utcnow()
        current_first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_first = current_first - timedelta(days=1)
        prev_first = prev_first.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        start = prev_first
        end = current_first  # أول اليوم الأول من الشهر الحالي (حصر [start, end))
        return start, end, start.strftime("%B %Y"), str(start.year)

    @staticmethod
    async def build_report(session, user_id: int) -> dict | None:
        """يبني تقرير الشهر السابق للمستخدم. يُرجع None إذا لا نشاط."""
        start, end, month_label, year = await MonthlyReportService._month_bounds()
        prev_month_start = start
        prev_month_end = end

        number_rows = await session.execute(
            select(NumberOrder).where(
                NumberOrder.user_id == user_id,
                NumberOrder.purchased_at >= prev_month_start,
                NumberOrder.purchased_at < prev_month_end,
            )
        )
        offers_rows = await session.execute(
            select(SpecialOfferOrder).where(
                SpecialOfferOrder.user_id == user_id,
                SpecialOfferOrder.created_at >= prev_month_start,
                SpecialOfferOrder.created_at < prev_month_end,
            )
        )
        number_orders = list(number_rows.scalars().all())
        offer_orders = list(offers_rows.scalars().all())

        if not number_orders and not offer_orders:
            return None

        total_spend = sum((o.price_sell_usd for o in number_orders), Decimal("0"))
        total_spend += sum((o.price_usd for o in offer_orders), Decimal("0"))
        total_orders = len(number_orders) + len(offer_orders)

        countries = Counter()
        services = Counter()
        for o in number_orders:
            countries[o.country_code] += 1
            services[o.service] += 1
        for o in offer_orders:
            services[f"عرض: {o.offer.name}"] += 1

        points = await MonthlyReportService._points_earned(session, user_id, prev_month_start)

        return {
            "month_label": month_label,
            "year": year,
            "total_orders": total_orders,
            "total_spend_usd": total_spend,
            "top_countries": countries.most_common(3),
            "top_services": services.most_common(3),
            "loyalty_points": points,
            "balance": (await session.get(User, user_id)).balance,
        }

    @staticmethod
    async def _points_earned(session, user_id: int, since: datetime) -> int:
        result = await session.execute(
            select(LoyaltyEvent).where(
                LoyaltyEvent.user_id == user_id,
                LoyaltyEvent.created_at >= since,
            )
        )
        return sum((e.points for e in result.scalars().all()), 0)

    @staticmethod
    async def render(session, user_id: int) -> str | None:
        report = await MonthlyReportService.build_report(session, user_id)
        if report is None:
            return None
        lines = [
            f"📊 <b>تقريرك الشهري — {report['month_label']}</b>\n",
            f"الطلبات: <b>{report['total_orders']}</b>",
            f"إجمالي الإنفاق: <b>{report['total_spend_usd']:g}$</b>",
            f"رصيدك الحالي: <b>{report['balance']:g}$</b>",
        ]
        if report["top_services"]:
            services = "\n".join(f"  • {name} ×{count}" for name, count in report["top_services"])
            lines.append(f"\n🛍 أكثر ما اشتريت:\n{services}")
        if report["top_countries"]:
            countries = "\n".join(
                f"  • {code} ×{count}" for code, count in report["top_countries"]
            )
            lines.append(f"\n🌍 أكثر الدول:\n{countries}")
        lines.append("\n✨ نواصل الإنجاز هذا الشهر!")
        return "\n".join(lines)