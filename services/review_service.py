"""تقييمات المنتجات بعد اكتمال الطلب."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from database.models import (
    ProductReview,
    UnifiedOrder,
    UnifiedOrderStatus,
)


class ReviewError(Exception):
    pass


class ReviewService:
    @staticmethod
    async def create(
        session,
        user_id: int,
        order_id: int,
        rating: int,
        comment: str | None = None,
    ) -> ProductReview:
        if rating < 1 or rating > 5:
            raise ReviewError("التقييم يجب أن يكون بين 1 و5.")
        order = await session.get(UnifiedOrder, order_id)
        if (
            order is None
            or order.user_id != user_id
            or order.status != UnifiedOrderStatus.COMPLETED
            or order.product_id is None
        ):
            raise ReviewError("يمكن تقييم الطلبات المكتملة فقط.")
        existing = await session.execute(
            select(ProductReview).where(
                ProductReview.user_id == user_id,
                ProductReview.product_id == order.product_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ReviewError("قيّمت هذا المنتج مسبقاً.")
        comment = comment.strip() if comment else None
        if comment and len(comment) > 500:
            raise ReviewError("التعليق طويل جداً.")
        review = ProductReview(
            user_id=user_id,
            product_id=order.product_id,
            unified_order_id=order_id,
            rating=rating,
            comment=comment,
        )
        session.add(review)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise ReviewError("قيّمت هذا المنتج مسبقاً.") from None
        await session.refresh(review)
        return review

    @staticmethod
    async def product_summary(session, product_id: int) -> tuple[Decimal | None, int]:
        result = await session.execute(
            select(
                func.avg(ProductReview.rating),
                func.count(ProductReview.id),
            ).where(ProductReview.product_id == product_id)
        )
        average, count = result.one()
        return (
            Decimal(str(average)).quantize(Decimal("0.1")) if average is not None else None,
            count,
        )
