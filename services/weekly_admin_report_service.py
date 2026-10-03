"""التقرير الأسبوعي للأدمن: أرقام الأسبوع في رسالة واحدة.

بدل أن يفتح الأدمن خمس شاشات ليعرف كيف كان الأسبوع، تصل رسالة
واحدة كل اثنين صباحاً فيها: الطلبات، الإيراد والربح، المستخدمون
الجدد، الإيداعات، الأكثر مبيعاً، وأكثر المزودين تعثراً — مع مقارنة
بالأسبوع الذي قبله حتى يعرف الاتجاه لا الرقم فقط.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from database.models import (
    ApiProvider,
    DepositRequest,
    DepositStatus,
    NumberOrder,
    Product,
    SupportTicket,
    SupportTicketStatus,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from services.feature_service import FeatureService
from services.html_guard import esc

logger = logging.getLogger(__name__)

FEATURE_KEY = "weekly_admin_report"

# الحالات التي تعني «مال محسوب» فعلاً
CHARGED_STATUSES = (
    UnifiedOrderStatus.PENDING,
    UnifiedOrderStatus.PROCESSING,
    UnifiedOrderStatus.PARTIAL,
    UnifiedOrderStatus.COMPLETED,
)


class WeeklyAdminReportService:
    """يبني تقرير الأسبوع ويرسله."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled(FEATURE_KEY, default=True)

    @staticmethod
    def _window(now: datetime | None = None, days: int = 7) -> tuple[datetime, datetime]:
        """نافذة الأسابيع: آخر ``days`` يوماً حتى الآن (اليوم محسوب)."""
        end = now or datetime.utcnow()
        start = end - timedelta(days=days)
        return start, end

    @staticmethod
    async def _period_stats(session, start: datetime, end: datetime) -> dict:
        orders = await session.scalar(
            select(func.count(UnifiedOrder.id)).where(
                UnifiedOrder.created_at >= start, UnifiedOrder.created_at < end
            )
        )
        completed = await session.scalar(
            select(func.count(UnifiedOrder.id)).where(
                UnifiedOrder.created_at >= start,
                UnifiedOrder.created_at < end,
                UnifiedOrder.status == UnifiedOrderStatus.COMPLETED,
            )
        )
        failed = await session.scalar(
            select(func.count(UnifiedOrder.id)).where(
                UnifiedOrder.created_at >= start,
                UnifiedOrder.created_at < end,
                UnifiedOrder.status == UnifiedOrderStatus.FAILED,
            )
        )
        revenue = await session.scalar(
            select(func.coalesce(func.sum(UnifiedOrder.price_usd), 0)).where(
                UnifiedOrder.created_at >= start,
                UnifiedOrder.created_at < end,
                UnifiedOrder.status.in_(CHARGED_STATUSES),
            )
        )
        cost = await session.scalar(
            select(func.coalesce(func.sum(UnifiedOrder.cost_price_usd), 0)).where(
                UnifiedOrder.created_at >= start,
                UnifiedOrder.created_at < end,
                UnifiedOrder.status.in_(CHARGED_STATUSES),
            )
        )
        numbers = await session.scalar(
            select(func.count(NumberOrder.id)).where(
                NumberOrder.purchased_at >= start, NumberOrder.purchased_at < end
            )
        )
        new_users = await session.scalar(
            select(func.count(User.id)).where(
                User.joined_at >= start, User.joined_at < end
            )
        )
        deposits = await session.scalar(
            select(func.coalesce(func.sum(DepositRequest.amount_usd), 0)).where(
                DepositRequest.status == DepositStatus.APPROVED,
                DepositRequest.processed_at.is_not(None),
                DepositRequest.processed_at >= start,
                DepositRequest.processed_at < end,
            )
        )
        tickets_opened = await session.scalar(
            select(func.count(SupportTicket.id)).where(
                SupportTicket.created_at >= start, SupportTicket.created_at < end
            )
        )
        tickets_closed = await session.scalar(
            select(func.count(SupportTicket.id)).where(
                SupportTicket.created_at >= start,
                SupportTicket.created_at < end,
                SupportTicket.status.in_(
                    (SupportTicketStatus.RESOLVED, SupportTicketStatus.CLOSED)
                ),
            )
        )
        revenue_d = Decimal(str(revenue or 0))
        cost_d = Decimal(str(cost or 0))
        return {
            "orders": int(orders or 0),
            "completed": int(completed or 0),
            "failed": int(failed or 0),
            "revenue": revenue_d,
            "profit": revenue_d - cost_d,
            "numbers": int(numbers or 0),
            "new_users": int(new_users or 0),
            "deposits": Decimal(str(deposits or 0)),
            "tickets_opened": int(tickets_opened or 0),
            "tickets_closed": int(tickets_closed or 0),
        }

    @staticmethod
    async def _top_products(session, start: datetime, end: datetime, limit: int = 5):
        rows = (
            await session.execute(
                select(
                    Product.name_ar,
                    func.count(UnifiedOrder.id).label("sold"),
                )
                .join(UnifiedOrder, UnifiedOrder.product_id == Product.id)
                .where(
                    UnifiedOrder.created_at >= start,
                    UnifiedOrder.created_at < end,
                    UnifiedOrder.status.in_(CHARGED_STATUSES),
                )
                .group_by(Product.id, Product.name_ar)
                .order_by(func.count(UnifiedOrder.id).desc())
                .limit(limit)
            )
        ).all()
        return [(str(name), int(count)) for name, count in rows]

    @staticmethod
    async def _failing_providers(session, start: datetime, end: datetime, limit: int = 3):
        rows = (
            await session.execute(
                select(
                    ApiProvider.name,
                    func.count(UnifiedOrder.id).label("fails"),
                )
                .join(UnifiedOrder, UnifiedOrder.api_provider_id == ApiProvider.id)
                .where(
                    UnifiedOrder.created_at >= start,
                    UnifiedOrder.created_at < end,
                    UnifiedOrder.status == UnifiedOrderStatus.FAILED,
                )
                .group_by(ApiProvider.id, ApiProvider.name)
                .order_by(func.count(UnifiedOrder.id).desc())
                .limit(limit)
            )
        ).all()
        return [(str(name), int(count)) for name, count in rows]

    @classmethod
    async def summary(cls, session, days: int = 7) -> dict:
        start, end = cls._window(days=days)  # آخر ٧ أيام حتى الآن
        prev_end = start
        prev_start = prev_end - timedelta(days=days)
        current = await cls._period_stats(session, start, end)
        previous = await cls._period_stats(session, prev_start, prev_end)
        return {
            "start": start,
            "end": end,
            "current": current,
            "previous": previous,
            "top_products": await cls._top_products(session, start, end),
            "failing_providers": await cls._failing_providers(session, start, end),
        }

    @staticmethod
    def _delta(current: Decimal | int, previous: Decimal | int) -> str:
        try:
            cur = Decimal(str(current))
            prev = Decimal(str(previous))
        except Exception:  # noqa: BLE001
            return ""
        if prev == 0:
            return "🆕" if cur > 0 else ""
        change = ((cur - prev) / prev) * Decimal("100")
        arrow = "🔺" if change > 0 else ("🔻" if change < 0 else "➖")
        return f"{arrow} {abs(change):.0f}%"

    @classmethod
    def render(cls, data: dict) -> str:
        if not data:
            return ""
        cur = data["current"]
        prev = data["previous"]
        start: datetime = data["start"]
        end: datetime = data["end"]
        lines = [
            "📊 <b>تقرير الأسبوع</b>",
            f"📅 {start.strftime('%d %b')} → {end.strftime('%d %b %Y')}",
            "",
            f"🧾 الطلبات: <b>{cur['orders']}</b> {cls._delta(cur['orders'], prev['orders'])}",
            f"   ✅ مكتملة {cur['completed']} · ❌ فاشلة {cur['failed']}",
            f"💰 الإيراد: <b>{cur['revenue']:.2f}$</b> "
            f"{cls._delta(cur['revenue'], prev['revenue'])}",
            f"📈 الربح التقريبي: <b>{cur['profit']:.2f}$</b>",
            f"📞 أرقام مباعة: <b>{cur['numbers']}</b>",
            f"👥 مستخدمون جدد: <b>{cur['new_users']}</b>",
            f"🏧 إيداعات مقبولة: <b>{cur['deposits']:.2f}$</b>",
            f"🎫 تذاكر: فُتحت {cur['tickets_opened']} · أُغلقت {cur['tickets_closed']}",
        ]
        top = data.get("top_products") or []
        if top:
            lines.append("")
            lines.append("🏆 <b>الأكثر مبيعاً</b>")
            for index, (name, sold) in enumerate(top, start=1):
                lines.append(f"   {index}) {esc(name)} — {sold}")
        failing = data.get("failing_providers") or []
        if failing:
            lines.append("")
            lines.append("⚠️ <b>أكثر المزودين تعثراً</b>")
            for name, fails in failing:
                lines.append(f"   • {esc(name)} — {fails} فشل")
        if not top and not failing and cur["orders"] == 0:
            lines.append("")
            lines.append("😴 أسبوع هادئ بلا طلبات.")
        return "\n".join(lines)

    @classmethod
    async def build(cls, session, days: int = 7) -> str:
        """نص التقرير جاهزاً للإرسال (يُرجع نصاً حتى لو تعذّر جزء منه)."""
        try:
            data = await cls.summary(session, days)
            return cls.render(data)
        except Exception:  # noqa: BLE001
            logger.exception("تعذّر بناء التقرير الأسبوعي")
            return "📊 <b>تقرير الأسبوع</b>\n\nتعذّر جمع بعض الأرقام هذه المرة."

    @classmethod
    async def cycle(cls, bot, days: int = 7) -> bool:
        if not await cls.enabled():
            return False
        from database.engine import async_session_maker
        from services.notification_service import NotificationService

        async with async_session_maker() as session:
            text = await cls.build(session, days=days)
        try:
            week = datetime.utcnow().strftime("%Y-W%W")
            ok = await NotificationService(bot).notify_admin(
                text, dedupe_key=f"weekly_admin_report:{week}"
            )
        except Exception:  # noqa: BLE001
            logger.exception("تعذّر إرسال التقرير الأسبوعي")
            return False
        return bool(ok)
