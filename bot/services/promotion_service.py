"""محرك العروض الزمنية الديناميكية."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select, update
from sqlalchemy.orm import selectinload

from database.models import (
    Product,
    ProductStatus,
    Promotion,
    PromotionDiscountType,
)


class PromotionService:
    @staticmethod
    def calculate_discount(
        promotion: Promotion,
        amount: Decimal,
    ) -> Decimal:
        if promotion.discount_type == PromotionDiscountType.PERCENT:
            discount = amount * promotion.discount_value / Decimal("100")
        else:
            discount = promotion.discount_value
        return min(amount, max(Decimal("0"), discount)).quantize(Decimal("0.0001"))

    @staticmethod
    async def get_active_promotions(
        session,
        limit: int = 30,
    ) -> list[Promotion]:
        now = datetime.utcnow()
        result = await session.execute(
            select(Promotion)
            .join(Promotion.product)
            .options(selectinload(Promotion.product))
            .where(
                Promotion.is_active.is_(True),
                Promotion.starts_at <= now,
                Promotion.ends_at > now,
                Product.status == ProductStatus.ACTIVE,
                or_(Promotion.max_uses == 0, Promotion.used_count < Promotion.max_uses),
            )
            .order_by(Promotion.ends_at, Promotion.id)
            .limit(limit)
        )
        return list(result.scalars().unique().all())

    @staticmethod
    async def get_active_for_product(
        session,
        product_id: int,
    ) -> list[Promotion]:
        now = datetime.utcnow()
        result = await session.execute(
            select(Promotion)
            .where(
                Promotion.product_id == product_id,
                Promotion.is_active.is_(True),
                Promotion.starts_at <= now,
                Promotion.ends_at > now,
                or_(Promotion.max_uses == 0, Promotion.used_count < Promotion.max_uses),
            )
            .order_by(Promotion.ends_at, Promotion.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_best_promotion(
        session,
        product_id: int,
        amount: Decimal,
    ) -> tuple[Promotion | None, Decimal]:
        promotions = await PromotionService.get_active_for_product(session, product_id)
        if not promotions:
            return None, Decimal("0")
        best = max(
            promotions,
            key=lambda promotion: PromotionService.calculate_discount(promotion, amount),
        )
        return best, PromotionService.calculate_discount(best, amount)

    @staticmethod
    async def mark_used(session, promotion_id: int) -> bool:
        """يزيد عداد العرض بعد نجاح الطلب (0 يعني استخدامات غير محدودة)."""
        result = await session.execute(
            update(Promotion)
            .where(
                Promotion.id == promotion_id,
                Promotion.is_active.is_(True),
                or_(Promotion.max_uses == 0, Promotion.used_count < Promotion.max_uses),
            )
            .values(used_count=Promotion.used_count + 1)
        )
        await session.commit()
        return result.rowcount == 1

    @staticmethod
    async def create(
        session,
        product_id: int,
        name: str,
        discount_type: PromotionDiscountType,
        discount_value: Decimal,
        starts_at: datetime,
        ends_at: datetime,
        created_by: int,
        max_uses: int = 0,
    ) -> Promotion:
        if ends_at <= starts_at:
            raise ValueError("وقت انتهاء العرض يجب أن يكون بعد بدايته.")
        promotion = Promotion(
            product_id=product_id,
            name=name[:128],
            discount_type=discount_type,
            discount_value=discount_value,
            starts_at=starts_at,
            ends_at=ends_at,
            max_uses=max(0, max_uses),
            created_by=created_by,
            is_active=True,
        )
        session.add(promotion)
        await session.commit()
        await session.refresh(promotion)
        return promotion

    @staticmethod
    async def toggle(session, promotion_id: int) -> Promotion | None:
        promotion = await session.get(Promotion, promotion_id)
        if promotion is None:
            return None
        promotion.is_active = not promotion.is_active
        await session.commit()
        await session.refresh(promotion)
        return promotion

    @staticmethod
    async def delete(session, promotion_id: int) -> bool:
        promotion = await session.get(Promotion, promotion_id)
        if promotion is None:
            return False
        await session.delete(promotion)
        await session.commit()
        return True
