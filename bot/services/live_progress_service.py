"""
لوحة التقدّم الحية لطلبات الرشق.

المشكلة التي يحلها هذا الملف:
`start_count` و`remains` مخزَّنان في `unified_orders` منذ البداية، لكن
المستخدم لا يراهما إلا في حالة `PARTIAL`. أي أن بيانات التقدّم موجودة
ومهملة.

الحل: حساب نسبة الإنجاز والسرعة والوقت المتوقع، وكشف الهبوط التلقائي
(نزول المتابعين بعد التنفيذ) لأن هذا هو أكثر ما يقلق العميل.

لا يُجرى أي طلب شبكة هنا — تُقرأ آخر حالة محفوظة، فتبقى الرخصة رخيصة
ويمكن استدعاؤها عند كل فتح للشاشة.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from database.models import UnifiedOrder, UnifiedOrderStatus
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class LiveProgressService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("live_progress")

    @staticmethod
    async def progress(session, order: UnifiedOrder) -> dict:
        """يحسب التقدّم من آخر حالة محفوظة للطلب."""
        quantity = int(order.quantity or 0)
        start = int(order.start_count or 0)
        remains = order.remains

        if quantity <= 0:
            return {
                "quantity": quantity,
                "delivered": 0,
                "remains": remains,
                "percent": 0.0,
                "status": _status_label(order.status),
                "has_drop": False,
                "eta_minutes": None,
                "speed_per_hour": None,
            }

        # ما نُفِّذ فعلياً = المطلوب - المتبقي
        delivered = quantity - int(remains) if remains is not None else start
        delivered = max(0, min(quantity, delivered))
        percent = round(delivered / quantity * 100, 1)

        speed = None
        eta = None
        if order.created_at is not None and delivered > 0:
            elapsed_minutes = max(
                1.0, (datetime.utcnow() - order.created_at).total_seconds() / 60
            )
            speed = round(delivered / elapsed_minutes * 60, 1)
            if remains is not None and int(remains) > 0 and speed > 0:
                eta = int(int(remains) / speed * 60)

        return {
            "quantity": quantity,
            "delivered": delivered,
            "remains": int(remains) if remains is not None else None,
            "percent": percent,
            "status": _status_label(order.status),
            "has_drop": delivered < start and start > 0,
            "dropped_by": max(0, start - delivered),
            "eta_minutes": eta,
            "speed_per_hour": speed,
        }

    @staticmethod
    def bar(percent: float, width: int = 10) -> str:
        """شريط تقدّم نصي لرسائل تليجرام."""
        filled = max(0, min(width, int(round(percent / 100 * width))))
        return "█" * filled + "░" * (width - filled)

    @staticmethod
    async def render(session, order: UnifiedOrder) -> str:
        """نص جاهز للعرض."""
        data = await LiveProgressService.progress(session, order)
        bar = LiveProgressService.bar(data["percent"])
        lines = [
            f"📊 <b>تقدّم الطلب #{order.id}</b>",
            "",
            f"{bar} <b>{data['percent']}%</b>",
            f"✅ نُفِّذ: {data['delivered']} من {data['quantity']}",
        ]
        if data["remains"] is not None:
            lines.append(f"⏳ متبقٍ: {data['remains']}")
        if data["speed_per_hour"]:
            lines.append(f"⚡ السرعة: {data['speed_per_hour']}/ساعة")
        if data["eta_minutes"]:
            lines.append(f"🕐 الوقت المتوقع: ~{data['eta_minutes']} دقيقة")
        if data["has_drop"] and await FeatureService.config_bool(
            "live_progress", "notify_on_drop", True
        ):
            lines.append(f"⚠️ لاحظنا هبوطاً مقداره {data['dropped_by']}")
        lines.append("")
        lines.append(f"الحالة: {data['status']}")
        return "\n".join(lines)

    @staticmethod
    async def detect_drops(session, min_drop: int = 1) -> list[UnifiedOrder]:
        """الطلبات التي هبطت بعد تنفيذها — تُمرَّر لضمان التعويض."""
        result = await session.execute(
            select(UnifiedOrder).where(
                UnifiedOrder.status.in_(
                    [UnifiedOrderStatus.COMPLETED, UnifiedOrderStatus.PARTIAL]
                ),
                UnifiedOrder.start_count.is_not(None),
                UnifiedOrder.remains.is_not(None),
            )
        )
        drops = []
        for order in result.scalars().all():
            data = await LiveProgressService.progress(session, order)
            if data["has_drop"] and data["dropped_by"] >= min_drop:
                drops.append(order)
        return drops


def _status_label(status) -> str:
    value = getattr(status, "value", status)
    return {
        "pending": "⏳ قيد الانتظار",
        "processing": "🔄 قيد التنفيذ",
        "completed": "✅ مكتمل",
        "partial": "⚠️ منجز جزئياً",
        "failed": "❌ فشل",
        "refunded": "↩️ مسترجع",
    }.get(str(value), str(value))
