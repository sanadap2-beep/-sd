"""
تقييم جودة خدمات الرشق والمخاطر التنبؤية من نتائج الطلبات الحقيقية.

المشكلتان اللتان يحلهما هذا الملف:
1) `services/quality_service.py` يقيس مزودي **الأرقام** فقط، فخدمات
   الرشق تُباع بلا أي مؤشر جودة رغم أن كل البيانات موجودة في
   `unified_orders`.
2) البوت يبيع الرقم ثم يتفرج. لا يحذّر المستخدم أن هذه الخدمة مع هذا
   المزود فشلها مرتفع، رغم أنه يملك السجل ليحسبها.

الحل: نفس المنطق، لكن محسوباً من نتائجك أنت لا من ادعاءات المزود.
- وسوم جودة تلقائية لكل خدمة: non-drop / سريع / عالي الهبوط.
- احتمال فشل يُعرض قبل الشراء، مع بديل أفضل إن وُجد.

كل الأرقام تُحسب من طلبات فعلية بحد أدنى من العيّنات، فلا يُوسم منتج
بلا دليل.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from database.models import (
    Product,
    ProductStatus,
    UnifiedOrder,
    UnifiedOrderStatus,
)
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class QualityScoreService:
    # ─────────── وسوم الجودة ───────────

    @staticmethod
    async def min_orders() -> int:
        return await FeatureService.config_int("smm_quality_score", "min_orders_to_rate", 10)

    @staticmethod
    async def score_product(session, product_id: int, days: int = 30) -> dict | None:
        """يحسب مؤشرات منتج من طلباته الفعلية."""
        if not await FeatureService.enabled("smm_quality_score"):
            return None

        since = datetime.utcnow() - timedelta(days=max(1, days))
        result = await session.execute(
            select(UnifiedOrder).where(
                UnifiedOrder.product_id == product_id,
                UnifiedOrder.created_at >= since,
            )
        )
        orders = list(result.scalars().all())
        minimum = await QualityScoreService.min_orders()
        if len(orders) < minimum:
            return None

        completed = dropped = refunded = failed = 0
        total_requested = 0
        total_delivered = 0

        for order in orders:
            status = getattr(order.status, "value", order.status)
            quantity = int(order.quantity or 0)
            total_requested += quantity
            if order.remains is not None:
                total_delivered += max(0, quantity - int(order.remains))
            if status == "completed":
                completed += 1
            elif status == "partial":
                dropped += 1
            elif status == "refunded":
                refunded += 1
            elif status == "failed":
                failed += 1

        total = len(orders)
        success_rate = round((completed + dropped) / total * 100, 1) if total else 0.0
        drop_rate = (
            round(dropped / total * 100, 1) if total else 0.0
        )
        fulfillment = (
            round(total_delivered / total_requested * 100, 1) if total_requested else 0.0
        )
        refund_rate = round(refunded / total * 100, 1) if total else 0.0

        return {
            "product_id": product_id,
            "orders": total,
            "success_rate": success_rate,
            "drop_rate": drop_rate,
            "fulfillment_rate": fulfillment,
            "refund_rate": refund_rate,
            "badge": QualityScoreService._badge(success_rate, drop_rate, fulfillment),
        }

    @staticmethod
    def _badge(success_rate: float, drop_rate: float, fulfillment: float) -> str:
        """وسم يُعرض للمستخدم، محسوب من بيانات لا من وصف المزود."""
        if success_rate >= 95 and drop_rate <= 5 and fulfillment >= 95:
            return "🛡️ Non-Drop"
        if success_rate >= 90 and fulfillment >= 90:
            return "💎 HQ"
        if success_rate >= 75:
            return "⚡ Fast Start"
        if drop_rate >= 25 or success_rate < 60:
            return "⚠️ High Drop"
        return "✅ Standard"

    @staticmethod
    async def rank_products(session, limit: int = 20) -> list[dict]:
        """يرتب المنتجات النشطة حسب جودتها المحسوبة."""
        result = await session.execute(
            select(Product.id).where(Product.status == ProductStatus.ACTIVE).limit(200)
        )
        scored = []
        for (product_id,) in result.all():
            score = await QualityScoreService.score_product(session, product_id)
            if score is not None:
                scored.append(score)
        scored.sort(
            key=lambda s: (s["success_rate"], -s["drop_rate"], s["fulfillment_rate"]),
            reverse=True,
        )
        return scored[:limit]


class PredictiveRiskService:
    """
    المخاطر التنبؤية: احتمال ألا ينجح الطلب قبل أن يدفع المستخدم.

    لا نموذج تعلم آلي هنا — إحصاء مباشر من سجل النتائج، وهو أدق مما
    يوحي به الاسم وأسهل في التدقيق. يُعرض تحذير حين تنخفض النسبة عن
    حد يضبطه الأدمن، مع اقتراح بديل أفضل.
    """

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("predictive_ban_risk")

    @staticmethod
    async def warn_below() -> int:
        return await FeatureService.config_int(
            "predictive_ban_risk", "warn_below_success_rate", 70
        )

    @staticmethod
    async def min_samples() -> int:
        return await FeatureService.config_int("predictive_ban_risk", "min_samples", 20)

    @staticmethod
    async def risk_for(session, product_id: int, days: int = 30) -> dict:
        """احتمال النجاح/الفشل لمنتج، مع توصية إن كان الخطر مرتفعاً."""
        if not await PredictiveRiskService.enabled():
            return {"available": False}

        since = datetime.utcnow() - timedelta(days=max(1, days))
        result = await session.execute(
            select(UnifiedOrder).where(
                UnifiedOrder.product_id == product_id,
                UnifiedOrder.created_at >= since,
            )
        )
        orders = list(result.scalars().all())
        samples = len(orders)
        if samples < await PredictiveRiskService.min_samples():
            return {"available": False, "samples": samples}

        bad = sum(
            1
            for order in orders
            if getattr(order.status, "value", order.status) in ("failed", "refunded")
        )
        failure_rate = round(bad / samples * 100, 1)
        success_rate = round(100 - failure_rate, 1)
        threshold = await PredictiveRiskService.warn_below()

        recommendation = None
        if success_rate < threshold:
            recommendation = await PredictiveRiskService._suggest_alternative(
                session, product_id, exclude_below=threshold
            )

        return {
            "available": True,
            "samples": samples,
            "success_rate": success_rate,
            "failure_rate": failure_rate,
            "risky": success_rate < threshold,
            "recommendation": recommendation,
        }

    @staticmethod
    async def _suggest_alternative(
        session, product_id: int, exclude_below: int
    ) -> dict | None:
        """يقترح منتجاً من نفس القسم الفرعي بجودة أفضل."""
        source = await session.get(Product, product_id)
        if source is None:
            return None
        result = await session.execute(
            select(Product).where(
                Product.id != product_id,
                Product.status == ProductStatus.ACTIVE,
                Product.sub_category_id == source.sub_category_id,
            )
        )
        best = None
        best_rate = exclude_below
        for candidate in result.scalars().all():
            score = await QualityScoreService.score_product(session, candidate.id)
            if score is None:
                continue
            if score["success_rate"] > best_rate:
                best_rate = score["success_rate"]
                best = (candidate, score)
        if best is None:
            return None
        candidate, score = best
        return {
            "product_id": candidate.id,
            "name": candidate.name_ar,
            "price_usd": candidate.price_usd,
            "success_rate": score["success_rate"],
            "badge": score["badge"],
        }
