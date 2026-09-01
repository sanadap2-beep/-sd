"""
خدمة كوبونات الخصم.
تتحقق من صلاحية الكوبون وتحسب الخصم وتسجل الاستخدام.
"""

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from database.models import Coupon, CouponUsage

logger = logging.getLogger(__name__)


class CouponError(Exception):
    pass


class CouponService:
    @staticmethod
    async def validate_coupon(
        session,
        code: str,
        user_id: int,
        order_amount_usd: Decimal,
    ) -> Coupon:
        """
        يتحقق من صلاحية الكوبون.
        يرمي CouponError مع رسالة واضحة إذا كان الكوبون غير صالح.
        """
        result = await session.execute(select(Coupon).where(Coupon.code == code.upper().strip()))
        coupon = result.scalar_one_or_none()

        if coupon is None:
            raise CouponError("❌ الكوبون غير موجود.")

        if not coupon.is_active:
            raise CouponError("❌ هذا الكوبون غير مفعل.")

        if coupon.expires_at and datetime.utcnow() > coupon.expires_at:
            raise CouponError("❌ انتهت صلاحية هذا الكوبون.")

        if coupon.used_count >= coupon.max_uses:
            raise CouponError("❌ تم استنفاد عدد استخدامات هذا الكوبون.")

        if order_amount_usd < coupon.min_order_usd:
            raise CouponError(f"❌ هذا الكوبون يتطلب حداً أدنى للطلب {coupon.min_order_usd}$.")

        already_used = await session.execute(
            select(CouponUsage).where(
                CouponUsage.coupon_id == coupon.id,
                CouponUsage.user_id == user_id,
            )
        )
        if already_used.scalar_one_or_none():
            raise CouponError("❌ لقد استخدمت هذا الكوبون مسبقاً.")

        return coupon

    @staticmethod
    def calculate_discount(
        coupon: Coupon,
        order_amount_usd: Decimal,
    ) -> Decimal:
        """
        يحسب مبلغ الخصم بالدولار.
        discount_type: percent → خصم نسبة مئوية
        discount_type: fixed  → خصم مبلغ ثابت
        """
        if coupon.discount_type == "percent":
            discount = order_amount_usd * (coupon.discount_value / Decimal("100"))
        else:
            discount = coupon.discount_value

        return min(discount, order_amount_usd).quantize(Decimal("0.0001"))

    @staticmethod
    async def apply_coupon(
        session,
        coupon: Coupon,
        user_id: int,
        discount_applied: Decimal,
    ) -> None:
        """Atomically reserve one coupon use and record the user usage."""
        # A Python-side ``used_count += 1`` loses updates when two users use
        # the last coupons at the same time. Let the database enforce the
        # remaining-use condition.
        result = await session.execute(
            update(Coupon)
            .where(
                Coupon.id == coupon.id,
                Coupon.is_active.is_(True),
                Coupon.used_count < Coupon.max_uses,
            )
            .values(used_count=Coupon.used_count + 1)
        )
        if result.rowcount != 1:
            raise CouponError("❌ تم استنفاد عدد استخدامات هذا الكوبون.")

        session.add(
            CouponUsage(
                coupon_id=coupon.id,
                user_id=user_id,
                discount_applied=discount_applied,
            )
        )

        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise CouponError("❌ لقد تم استخدام هذا الكوبون مسبقاً.") from None

        logger.info(f"تم تطبيق الكوبون {coupon.code} للمستخدم {user_id} بخصم {discount_applied}$")

    @staticmethod
    async def get_coupon_by_code(session, code: str) -> Coupon | None:
        result = await session.execute(select(Coupon).where(Coupon.code == code.upper().strip()))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all_coupons(session) -> list[Coupon]:
        result = await session.execute(select(Coupon).order_by(Coupon.created_at.desc()))
        return list(result.scalars().all())

    @staticmethod
    async def create_coupon(
        session,
        code: str,
        discount_type: str,
        discount_value: Decimal,
        max_uses: int,
        created_by: int,
        min_order_usd: Decimal = Decimal("0"),
        expires_at: datetime | None = None,
    ) -> Coupon:
        coupon = Coupon(
            code=code.upper().strip(),
            discount_type=discount_type,
            discount_value=discount_value,
            max_uses=max_uses,
            min_order_usd=min_order_usd,
            expires_at=expires_at,
            created_by=created_by,
            is_active=True,
        )
        session.add(coupon)
        await session.commit()
        await session.refresh(coupon)
        return coupon

    @staticmethod
    async def toggle_coupon(session, coupon_id: int) -> Coupon | None:
        coupon = await session.get(Coupon, coupon_id)
        if coupon is None:
            return None
        coupon.is_active = not coupon.is_active
        await session.commit()
        await session.refresh(coupon)
        return coupon

    @staticmethod
    async def delete_coupon(session, coupon_id: int) -> bool:
        coupon = await session.get(Coupon, coupon_id)
        if coupon is None:
            return False
        await session.delete(coupon)
        await session.commit()
        return True
