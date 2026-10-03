"""نبضة سريعة للوحة الأدمن: أرقام اليوم في سطرين قبل التبويبات.

الهدف: الأدمن يفتح اللوحة فيعرف فوراً «كم بعنا اليوم وكم شيء ينتظرني»
بدل التنقيب في شاشة الإحصائيات. كل الأرقام اختيارية: أي فشل يُرجع
قاموساً فارغاً ولا يُسقط لوحة التحكم (اللوحة أهم من الأرقام).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select

from database.models import (
    DepositRequest,
    DepositStatus,
    NumberOrder,
    ProductRequest,
    ProductRequestStatus,
    SupportTicket,
    SupportTicketStatus,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)

# الطلبات التي دُفعت فعلاً (المسترجَعة والفاشلة لا تُحتسب إيراداً).
CHARGED_STATUSES = (
    UnifiedOrderStatus.PENDING,
    UnifiedOrderStatus.PROCESSING,
    UnifiedOrderStatus.PARTIAL,
    UnifiedOrderStatus.COMPLETED,
)


class AdminPulseService:
    """ملخّص لحظي لطلبات اليوم وإيراده وما ينتظر الأدمن."""

    @staticmethod
    async def summary(session) -> dict:
        now = datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        data: dict = {}
        try:
            data["orders_today"] = int(
                await session.scalar(
                    select(func.count(UnifiedOrder.id)).where(UnifiedOrder.created_at >= today)
                )
                or 0
            )
            data["numbers_today"] = int(
                await session.scalar(
                    select(func.count(NumberOrder.id)).where(NumberOrder.purchased_at >= today)
                )
                or 0
            )
            revenue = await session.scalar(
                select(func.coalesce(func.sum(UnifiedOrder.price_usd), 0)).where(
                    UnifiedOrder.created_at >= today,
                    UnifiedOrder.status.in_(CHARGED_STATUSES),
                )
            )
            data["revenue_today"] = Decimal(str(revenue or 0)).quantize(Decimal("0.01"))

            data["pending_orders"] = int(
                await session.scalar(
                    select(func.count(UnifiedOrder.id)).where(
                        UnifiedOrder.status == UnifiedOrderStatus.PENDING
                    )
                )
                or 0
            )
            data["pending_deposits"] = int(
                await session.scalar(
                    select(func.count(DepositRequest.id)).where(
                        DepositRequest.status == DepositStatus.PENDING
                    )
                )
                or 0
            )
            data["open_tickets"] = int(
                await session.scalar(
                    select(func.count(SupportTicket.id)).where(
                        SupportTicket.status.in_(
                            (SupportTicketStatus.OPEN, SupportTicketStatus.IN_PROGRESS)
                        )
                    )
                )
                or 0
            )
            data["open_requests"] = int(
                await session.scalar(
                    select(func.count(ProductRequest.id)).where(
                        ProductRequest.status == ProductRequestStatus.OPEN
                    )
                )
                or 0
            )
            data["new_users_today"] = int(
                await session.scalar(
                    select(func.count(User.id)).where(User.joined_at >= today)
                )
                or 0
            )
        except Exception:  # noqa: BLE001 — اللوحة لا تُسقط بسبب رقم
            return data
        return data

    @staticmethod
    def render(data: dict) -> str:
        """سطرا النبضة بصيغة HTML (فارغ إن لم تتوفر أرقام)."""
        if not data:
            return ""
        lines = ["📊 <b>نبضة اليوم</b>"]
        lines.append(
            f"🧾 طلبات: <b>{data.get('orders_today', 0)}</b>"
            f" · 📞 أرقام: <b>{data.get('numbers_today', 0)}</b>"
            f" · 💰 <b>{data.get('revenue_today', 0)}$</b>"
        )
        waiting = []
        if data.get("pending_orders"):
            waiting.append(f"{data['pending_orders']} طلب")
        if data.get("pending_deposits"):
            waiting.append(f"{data['pending_deposits']} إيداع")
        if data.get("open_tickets"):
            waiting.append(f"{data['open_tickets']} تذكرة")
        if data.get("open_requests"):
            waiting.append(f"{data['open_requests']} طلب منتج")
        lines.append(
            "⏳ بانتظارك: " + (" · ".join(waiting) if waiting else "لا شيء 👍")
        )
        lines.append(f"👥 مستخدمون جدد اليوم: <b>{data.get('new_users_today', 0)}</b>")
        return "\n".join(lines)
