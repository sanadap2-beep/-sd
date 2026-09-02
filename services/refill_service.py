"""
ضمان التعويض الآلي لطلبات الرشق.

المشكلة التي يصلحها هذا الملف:
`refill_order()` كانت منفَّذة في protocols/smm_v2.py:456 منذ البداية،
و`supports_refill` محفوظ لكل خدمة مسحوبة من المزود — لكن **لا شيء
في البوت كان يستدعيها**. أي أن أقوى ميزة تنافسية في سوق الرشق كانت
كوداً ميتاً.

كيف يعمل الآن:
1) بعد اكتمال الطلب يُسجَّل في «فترة الضمان» (افتراضياً 30 يوماً).
2) مهمة خلفية تعيد فحص الطلب لدى المزود كل N ساعة.
3) إن نزل العدد المنفَّذ عن المطلوب (هبوط المتابعين) واستخدم المزود
   خدمة تدعم refill، يُستدعى refill_order() تلقائياً.
4) كل محاولة تُسجَّل فلا يُعاد التعويض مرات بلا نهاية.
5) كل السلوك قابل للإيقاف والضبط من «مركز الإضافات».
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from database.models import (
    Product,
    ProviderService,
    UnifiedOrder,
    UnifiedOrderStatus,
)
from protocols.base import ProtocolError
from protocols.factory import ProtocolFactory
from services.feature_service import FeatureService
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class RefillService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("refill_guarantee")

    @staticmethod
    async def guarantee_days() -> int:
        return await FeatureService.config_int("refill_guarantee", "guarantee_days", 30)

    @staticmethod
    async def max_refills_per_order() -> int:
        """حاجز ضد التعويض اللانهائي على نفس الطلب."""
        return await FeatureService.config_int("refill_guarantee", "max_refills_per_order", 3)

    @staticmethod
    async def due_orders(session) -> list[UnifiedOrder]:
        """
        الطلبات المكتملة التي ما زالت داخل فترة الضمان.
        """
        if not await RefillService.enabled():
            return []
        days = await RefillService.guarantee_days()
        if days <= 0:
            return []
        cutoff = datetime.utcnow() - timedelta(days=days)
        result = await session.execute(
            select(UnifiedOrder).where(
                UnifiedOrder.status.in_(
                    [UnifiedOrderStatus.COMPLETED, UnifiedOrderStatus.PARTIAL]
                ),
                UnifiedOrder.completed_at.is_not(None),
                UnifiedOrder.completed_at >= cutoff,
                UnifiedOrder.external_order_id.is_not(None),
                UnifiedOrder.api_provider_id.is_not(None),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def supports_refill(session, order: UnifiedOrder) -> bool:
        """هل خدمة المزود المرتبطة بهذا المنتج تدعم التعويض أصلاً؟"""
        # لا نستخدم order.product: الوصول إلى علاقة غير محمّلة يُطلق
        # lazy load داخل سياق async فيرمي MissingGreenlet.
        if not order.product_id:
            return False
        product = await session.get(Product, order.product_id)
        if product is None or not product.provider_service_ref_id:
            return False
        service = await session.get(ProviderService, product.provider_service_ref_id)
        return bool(service and service.supports_refill)

    @staticmethod
    async def check_and_refill(session, order: UnifiedOrder, bot=None) -> dict | None:
        """
        يفحص الطلب لدى المزود، وإن كان هناك هبوط يطلب التعويض.
        يرجع تقريراً أو None إن لم يلزم شيء.
        """
        if not await RefillService.enabled():
            return None
        if not order.api_provider or not order.external_order_id:
            return None

        attempts = int(order.refill_attempts or 0)
        if attempts >= await RefillService.max_refills_per_order():
            return None
        if not await RefillService.supports_refill(session, order):
            return None

        # لا نعيد الفحص قبل انقضاء الفترة المحددة
        interval_hours = await FeatureService.config_int(
            "refill_guarantee", "recheck_interval_hours", 12
        )
        if order.last_refill_check_at is not None:
            elapsed = (datetime.utcnow() - order.last_refill_check_at).total_seconds() / 3600
            if elapsed < max(1, interval_hours):
                return None

        min_drop = await FeatureService.config_int("refill_guarantee", "min_drop_to_refill", 1)

        try:
            protocol = ProtocolFactory.create_from_provider(order.api_provider)
            status = await protocol.check_order_status(order.external_order_id)
        except (ProtocolError, Exception) as exc:  # noqa: BLE001
            logger.debug("تعذّر فحص الطلب %s للتعويض: %s", order.id, exc)
            return None

        order.last_refill_check_at = datetime.utcnow()

        remains = status.remains
        if remains is None:
            await session.commit()
            return None

        if remains < min_drop:
            await session.commit()
            return None

        # ── طلب التعويض ──
        try:
            ok = await protocol.refill_order(order.external_order_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("فشل طلب تعويض الطلب %s: %s", order.id, exc)
            await session.commit()
            return None

        order.refill_attempts = attempts + 1
        order.remains = remains
        await session.commit()

        report = {
            "order_id": order.id,
            "remains": remains,
            "refilled": bool(ok),
            "attempts": order.refill_attempts,
        }
        await FeatureService.track(
            "refill_guarantee", "refilled" if ok else "refill_failed", value=str(order.id)
        )

        if bot is not None and ok:
            try:
                from database.models import User

                user = await session.get(User, order.user_id)
                if user is not None:
                    await NotificationService(bot).notify_user(
                        user.telegram_id,
                        "🛡️ <b>ضمان التعويض</b>\n\n"
                        f"🆔 الطلب: #{order.id}\n"
                        f"📉 لاحظنا هبوطاً مقداره <b>{remains}</b>.\n"
                        "✅ طلبنا التعويض من المزود تلقائياً، بلا أي إجراء منك.",
                    )
            except Exception:  # noqa: BLE001
                pass
        return report

    @staticmethod
    async def run_cycle(session, bot=None) -> dict:
        """دورة كاملة تُنادى من المجدول."""
        stats = {"checked": 0, "refilled": 0, "failed": 0}
        if not await RefillService.enabled():
            return stats
        for order in await RefillService.due_orders(session):
            report = await RefillService.check_and_refill(session, order, bot=bot)
            if report is None:
                continue
            stats["checked"] += 1
            if report["refilled"]:
                stats["refilled"] += 1
            else:
                stats["failed"] += 1
        if stats["checked"]:
            logger.info(
                "ضمان التعويض: فُحص %s، عُوّض %s، فشل %s",
                stats["checked"], stats["refilled"], stats["failed"],
            )
        return stats
