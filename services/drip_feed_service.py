"""
التدريج المجدول (Drip-Feed) لطلبات الرشق.

المشكلة التي يحلها هذا الملف:
بحث `drip|schedule|runs` كان صفراً. طلب 10,000 متابع يُنفَّذ دفعة
واحدة، وهذا **يضرّ حساب العميل** ويخيفه. كل لوحة رشق جادة تعرض
التدريج، فبدونه البوت خارج حسابات العميل الجاد أصلاً.

الحل: يُقسَّم الطلب إلى دفعات مجدولة، وتُنشأ عملية مستقلة لكل دفعة
عند حلول موعدها، فيتوزّع التنفيذ على الزمن بدل لحظة واحدة.

لماذا لا يضيع المال: تُخصم القيمة الكاملة مقدماً وتُسجَّل الدفعات
مسبقاً بحالة pending، فإما تُنفَّذ كلها أو تُسترجع قيمة ما لم يُنفَّذ.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from database.models import (
    ApiProvider,
    Product,
    UnifiedOrder,
    UnifiedOrderStatus,
)
from protocols.base import ProtocolError
from protocols.factory import ProtocolFactory
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class DripFeedError(Exception):
    pass


class DripFeedService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("drip_feed")

    @staticmethod
    async def max_runs() -> int:
        return max(1, await FeatureService.config_int("drip_feed", "max_runs", 50))

    @staticmethod
    async def min_interval_minutes() -> int:
        return max(1, await FeatureService.config_int("drip_feed", "min_interval_minutes", 30))

    @staticmethod
    def plan(quantity: int, runs: int) -> list[int]:
        """
        يوزّع الكمية على عدد الدفعات بحيث لا يضيع الباقي.
        plan(10, 3) -> [4, 3, 3]
        """
        if runs <= 0:
            return [quantity]
        base, remainder = divmod(quantity, runs)
        return [base + (1 if index < remainder else 0) for index in range(runs)]

    @staticmethod
    async def schedule(
        session,
        order: UnifiedOrder,
        runs: int,
        interval_minutes: int,
    ) -> list[UnifiedOrder]:
        """يحوّل طلباً واحداً إلى دفعة مجدولة."""
        if not await DripFeedService.enabled():
            raise DripFeedError("التدريج موقوف حالياً.")
        if runs < 2:
            raise DripFeedError("التدريج يتطلب دفعتين على الأقل.")
        if runs > await DripFeedService.max_runs():
            raise DripFeedError(f"الحد الأقصى {await DripFeedService.max_runs()} دفعة.")
        if interval_minutes < await DripFeedService.min_interval_minutes():
            raise DripFeedError(
                f"أقل فاصل بين الدفعتين {await DripFeedService.min_interval_minutes()} دقيقة."
            )

        quantity = int(order.quantity or 0)
        if quantity < runs:
            raise DripFeedError("الكمية أقل من عدد الدفعات.")

        sizes = DripFeedService.plan(quantity, runs)
        now = datetime.utcnow()
        created: list[UnifiedOrder] = []

        for index, size in enumerate(sizes):
            if size <= 0:
                continue
            run = UnifiedOrder(
                user_id=order.user_id,
                product_id=order.product_id,
                api_provider_id=order.api_provider_id,
                promotion_id=None,
                external_order_id=None,
                target=order.target,
                quantity=size,
                price_usd=order.price_usd,
                cost_price_usd=order.cost_price_usd,
                status=UnifiedOrderStatus.PENDING,
                status_message=f"دفعة {index + 1}/{runs} مجدولة",
                drip_run_index=index + 1,
                drip_total_runs=runs,
                drip_parent_id=order.id,
                drip_scheduled_for=now + timedelta(minutes=interval_minutes * index),
            )
            session.add(run)
            created.append(run)

        # الطلب الأصلي يصير مظلة لا تُنفَّذ مرتين
        order.status = UnifiedOrderStatus.PROCESSING
        order.status_message = f"مقسّم على {runs} دفعات"
        order.quantity = 0
        await session.commit()

        await FeatureService.track(
            "drip_feed", "scheduled", user_id=order.user_id, value=f"{runs}x{quantity}"
        )
        return created

    @staticmethod
    async def run_due(session) -> dict:
        """ينفّذ الدفعات التي حلّ موعدها."""
        stats = {"executed": 0, "failed": 0}
        if not await DripFeedService.enabled():
            return stats

        now = datetime.utcnow()
        result = await session.execute(
            select(UnifiedOrder).where(
                UnifiedOrder.status == UnifiedOrderStatus.PENDING,
                UnifiedOrder.drip_parent_id.is_not(None),
                UnifiedOrder.drip_scheduled_for.is_not(None),
                UnifiedOrder.drip_scheduled_for <= now,
            )
        )
        for run in result.scalars().all():
            provider = (
                await session.get(ApiProvider, run.api_provider_id)
                if run.api_provider_id
                else None
            )
            product = await session.get(Product, run.product_id)
            if provider is None or not provider.is_active or product is None:
                run.status = UnifiedOrderStatus.FAILED
                run.status_message = "المزود غير متاح لهذه الدفعة"
                stats["failed"] += 1
                continue
            try:
                protocol = ProtocolFactory.create_from_provider(provider)
                external = await protocol.place_order(
                    service_id=product.provider_service_id,
                    target=run.target,
                    quantity=run.quantity,
                )
                run.external_order_id = external.external_order_id
                run.status = UnifiedOrderStatus.PROCESSING
                run.status_message = "جارٍ تنفيذ الدفعة"
                stats["executed"] += 1
            except (ProtocolError, Exception) as exc:  # noqa: BLE001
                logger.warning("فشلت دفعة تدريج %s: %s", run.id, exc)
                run.status = UnifiedOrderStatus.FAILED
                run.status_message = f"فشل التنفيذ: {str(exc)[:120]}"
                stats["failed"] += 1

        await session.commit()
        if any(stats.values()):
            logger.info("التدريج: نُفِّذ %s، فشل %s", stats["executed"], stats["failed"])
        return stats

    @staticmethod
    async def progress(session, parent_id: int) -> dict:
        """تقدّم حملة التدريج ككل."""
        result = await session.execute(
            select(UnifiedOrder).where(UnifiedOrder.drip_parent_id == parent_id)
        )
        runs = list(result.scalars().all())
        if not runs:
            return {"runs": 0, "done": 0, "pending": 0, "failed": 0, "percent": 0.0}
        done = sum(
            1
            for run in runs
            if getattr(run.status, "value", run.status)
            in ("completed", "partial", "processing")
        )
        failed = sum(
            1
            for run in runs
            if getattr(run.status, "value", run.status) == "failed"
        )
        pending = len(runs) - done - failed
        return {
            "runs": len(runs),
            "done": done,
            "pending": pending,
            "failed": failed,
            "percent": round(done / len(runs) * 100, 1),
        }
