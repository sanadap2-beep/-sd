"""
خدمة العزل الذاتي للمزود المعطوب (auto_failover).

تتابع فشل المسارات المتتالية لكل منتج: إذا تجاوز عدد الفشل الحد (
FeatureService config) تُعزل المسار المعطوب تلقائياً ويُصفَّر العدد.
تتلاشى العدادات تلقائياً مع مرور الوقت لمنع الإغلاق الدائم.

المدخلات: checkout_service و games._finalize_purchase عند كل ProtocolError.
المخرجات: عزل (ProductProviderRoute.is_active=False) + إشعار أدمن.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from sqlalchemy import select

from database.models import ApiProvider, ProductProviderRoute
from services.feature_service import FeatureService
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class AutoFailoverService:
    """عدادات فشل بالذاكرة لكل (product_id, api_provider_id)."""

    _failures: dict[tuple[int, int], int] = defaultdict(int)
    _isolated: set[tuple[int, int]] = set()
    _lock = asyncio.Lock()

    @classmethod
    async def enabled(cls) -> bool:
        return await FeatureService.enabled("auto_failover")

    @classmethod
    async def threshold(cls) -> int:
        return max(1, await FeatureService.config_int("auto_failover", "failure_threshold", 3))

    @classmethod
    async def record_failure(
        cls,
        session,
        product_id: int,
        api_provider_id: int,
        *,
        is_backup_route: bool,
        bot=None,
    ) -> bool:
        """
        تُسجّل فشل مسار وتعزله إذا تجاوز الحد.
        تُرجع True إذا عُزِل المسار.
        """
        if not await cls.enabled():
            return False

        key = (product_id, api_provider_id)
        async with cls._lock:
            cls._failures[key] += 1
            count = cls._failures[key]

        limit = await cls.threshold()
        if count < limit:
            return False

        if key in cls._isolated:
            return False
        cls._isolated.add(key)

        if not is_backup_route:
            logger.warning(
                "auto_failover: المنتج %s فشل %d مرات مع المزود %s الأساسي "
                "(حد = %d) — يُنذر الأدمن فقط.",
                product_id, count, api_provider_id, limit,
            )
            if bot:
                try:
                    from services.i18n_service import I18nService

                    await NotificationService(bot).notify_admin(
                        f"⚠️ <b>تنبيه auto_failover — مزود أساسي</b>\n\n"
                        f"📦 المنتج #{product_id}\n"
                        f"🔌 المزود #{api_provider_id}\n"
                        f"❌ فشل متكرر ({count} مرات)\n\n"
                        "لم يُعَزل تلقائياً لأنه مزود أساسي — راجع يدوياً.",
                    )
                except Exception:
                    pass
            return False

        # مسار احتياطي → عزل وهمش
        try:
            result = await session.execute(
                select(ProductProviderRoute).where(
                    ProductProviderRoute.product_id == product_id,
                    ProductProviderRoute.api_provider_id == api_provider_id,
                    ProductProviderRoute.is_active.is_(True),
                )
            )
            route = result.scalar_one_or_none()
            if route is not None:
                route.is_active = False
                await session.commit()
        except Exception as exc:
            logger.warning("auto_failover: تعذر عزل المسار %s/%s: %s", product_id, api_provider_id, exc)

        logger.info(
            "auto_failover: عُزِل المسار الاحتياطي %s/%s للمنتج %s بعد %d فشل.",
            product_id, api_provider_id, product_id, count,
        )

        if bot:
            try:
                provider = await session.get(ApiProvider, api_provider_id)
                provider_name = getattr(provider, "name", f"#{api_provider_id}")
                await NotificationService(bot).notify_admin(
                    f"🛡 <b>auto_failover — عزل مسار تلقائي</b>\n\n"
                    f"📦 المنتج #{product_id}\n"
                    f"🔌 المزود: {provider_name} (#{api_provider_id})\n"
                    f"❌ فشل متكرر ({count} مرات)\n"
                    "✅ تم تعطيل المسار الاحتياطي تلقائياً.\n\n"
                    "يمكنك إعادة تفعيله من: إدارة المنتجات → المسارات."
                )
            except Exception:
                pass

        return True

    @classmethod
    async def decay(cls) -> int:
        """تقلّص جميع العدادات للنصف (تمنع الإغلاق الدائم)."""
        async with cls._lock:
            removed = 0
            keys_to_remove = []
            for key in list(cls._failures.keys()):
                half = cls._failures[key] // 2
                if half <= 0:
                    cls._failures.pop(key, None)
                    cls._isolated.discard(key)
                    keys_to_remove.append(key)
                else:
                    cls._failures[key] = half
            removed = len(keys_to_remove)
        if removed:
            logger.info("auto_failover: decay أزالت %d عداد متوقف.", removed)
        return removed
