"""
خدمة تقييم المزودين.

بعد كل طلب مكتمل يُدعى المستخدم للتقييم (1-5 نجوم + تعليق اختياري).
تُلخَّص النتائج في متوسط واضح يظهر قبل عملية الشراء في نفس البوت.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import func, select

from database.models import ProviderReview
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class ReviewError(Exception):
    """خطأ واضح في تقييم المزود."""


class ProviderReviewService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("provider_reviews")

    @staticmethod
    async def user_reviews(session, user_id: int, limit: int = 20) -> list[ProviderReview]:
        result = await session.execute(
            select(ProviderReview)
            .where(ProviderReview.user_id == user_id)
            .order_by(ProviderReview.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def already_reviewed(session, user_id: int, order_type: str, order_id: int) -> bool:
        result = await session.execute(
            select(ProviderReview.id).where(
                ProviderReview.user_id == user_id,
                ProviderReview.order_type == order_type,
                ProviderReview.order_id == order_id,
            )
        )
        return result.scalar_one_or_none() is not None

    @staticmethod
    async def add_review(
        session, user_id: int, provider: str, order_type: str, order_id: int, rating: int, comment: str | None
    ) -> ProviderReview:
        if rating < 1 or rating > 5:
            raise ReviewError("التقييم يجب أن يكون بين 1 و 5 نجوم.")
        if await ProviderReviewService.already_reviewed(session, user_id, order_type, order_id):
            raise ReviewError("قيمت هذا الطلب من قبل.")

        review = ProviderReview(
            user_id=user_id,
            provider=provider[:64],
            order_type=order_type,
            order_id=int(order_id),
            rating=rating,
            comment=(comment or None),
        )
        session.add(review)
        await session.commit()
        await session.refresh(review)
        return review

    @staticmethod
    async def avg_for_provider(session, provider: str) -> tuple[Decimal, int] | None:
        """متوسط التقييم وعدد التقييمات لمزود معين."""
        result = await session.execute(
            select(func.avg(ProviderReview.rating), func.count(ProviderReview.id)).where(
                ProviderReview.provider == provider
            )
        )
        row = result.one()
        if row[1] == 0 or row[0] is None:
            return None
        return Decimal(str(round(row[0], 2))), int(row[1])

    @staticmethod
    async def low_rated(session, provider: str, min_rating: int = 3) -> bool:
        """هل المزود تحت العتبة (للفحص الآلي للجودة)؟"""
        avg = await ProviderReviewService.avg_for_provider(session, provider)
        if avg is None:
            return False
        return avg[0] < min_rating

    @staticmethod
    async def recent_reviews(session, provider: str, limit: int = 5) -> list[ProviderReview]:
        result = await session.execute(
            select(ProviderReview)
            .where(ProviderReview.provider == provider)
            .order_by(ProviderReview.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())