"""
خدمة أكواد الحملات مع تتبّع المصدر.

الكوبونات التقليدية مجهولة المصدر — لا يعرف الأدمن كم طلب جلبت حملة
إعلانية معينة. أكواد الحملات تُضيف وسماً (tracking) يربط كل استخدام
برسالة محددة، فيرى الأدمن: حملة X جلبت 15 طلب وحملة Y جلبت 3 طلبات.

الفرق ببساطة:
- Coupon: مجهول المصدر — لا تتبّع.
- CampaignCode: يحمل وسماً (tracking) — خصم لكل شخص مرة واحدة مع تتبّع.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from database.models import CampaignCode, CampaignCodeUsage

logger = logging.getLogger(__name__)


class CampaignCodeError(Exception):
    pass


class CampaignService:
    @staticmethod
    async def validate(
        session,
        code: str,
        user_id: int,
        order_amount_usd: Decimal,
    ) -> CampaignCode:
        """يتحقق من صلاحية كود الحملة ويرمي CampaignCodeError."""
        result = await session.execute(
            select(CampaignCode).where(CampaignCode.code == code.upper().strip())
        )
        campaign = result.scalar_one_or_none()

        if campaign is None:
            raise CampaignCodeError("❌ كود الحملة غير موجود.")

        if not campaign.is_active:
            raise CampaignCodeError("❌ هذا الكود غير مفعل.")

        if campaign.expires_at and datetime.utcnow() > campaign.expires_at:
            raise CampaignCodeError("❌ انتهت صلاحية هذا الكود.")

        if campaign.used_count >= campaign.max_uses:
            raise CampaignCodeError("❌ تم استنفاد استخدامات هذا الكود.")

        if order_amount_usd < campaign.min_order_usd:
            raise CampaignCodeError(
                f"❌ هذا الكود يتطلب حداً أدنى للطلب {campaign.min_order_usd}$."
            )

        already_used = await session.execute(
            select(CampaignCodeUsage).where(
                CampaignCodeUsage.campaign_id == campaign.id,
                CampaignCodeUsage.user_id == user_id,
            )
        )
        if already_used.scalar_one_or_none():
            raise CampaignCodeError("❌ لقد استخدمت هذا الكود مسبقاً.")

        return campaign

    @staticmethod
    def calculate_discount(
        campaign: CampaignCode,
        order_amount_usd: Decimal,
    ) -> Decimal:
        if campaign.discount_type == "percent":
            discount = order_amount_usd * (campaign.discount_value / Decimal("100"))
        else:
            discount = campaign.discount_value
        return min(discount, order_amount_usd).quantize(Decimal("0.0001"))

    @staticmethod
    async def apply(
        session,
        campaign: CampaignCode,
        user_id: int,
        discount_applied: Decimal,
    ) -> None:
        """يحتفظ بعدد الاستخدام ويسجل سجل الاستخدام لكل مستخدم."""
        result = await session.execute(
            update(CampaignCode)
            .where(
                CampaignCode.id == campaign.id,
                CampaignCode.is_active.is_(True),
                CampaignCode.used_count < CampaignCode.max_uses,
            )
            .values(used_count=CampaignCode.used_count + 1)
        )
        if result.rowcount != 1:
            raise CampaignCodeError("❌ تم استنفاد استخدامات هذا الكود.")

        session.add(
            CampaignCodeUsage(
                campaign_id=campaign.id,
                user_id=user_id,
                discount_applied=discount_applied,
            )
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise CampaignCodeError("❌ لقد تم استخدام هذا الكود مسبقاً.") from None

        logger.info(
            "تم تطبيق كود الحملة %s للمستخدم %s بخصم %s$",
            campaign.code, user_id, discount_applied,
        )

    @staticmethod
    async def get_by_code(session, code: str) -> CampaignCode | None:
        result = await session.execute(
            select(CampaignCode).where(CampaignCode.code == code.upper().strip())
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(session) -> list[CampaignCode]:
        result = await session.execute(
            select(CampaignCode).order_by(CampaignCode.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_by_tracking(session, tracking: str) -> list[CampaignCode]:
        result = await session.execute(
            select(CampaignCode).where(CampaignCode.tracking == tracking.strip())
        )
        return list(result.scalars().all())

    @staticmethod
    async def create(
        session,
        *,
        code: str,
        tracking: str,
        discount_type: str,
        discount_value: Decimal,
        max_uses: int,
        created_by: int,
        min_order_usd: Decimal = Decimal("0"),
        expires_at: datetime | None = None,
    ) -> CampaignCode:
        campaign = CampaignCode(
            code=code.upper().strip(),
            tracking=tracking.strip(),
            discount_type=discount_type,
            discount_value=discount_value,
            max_uses=max_uses,
            min_order_usd=min_order_usd,
            expires_at=expires_at,
            created_by=created_by,
            is_active=True,
        )
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)
        return campaign

    @staticmethod
    async def toggle(session, campaign_id: int) -> CampaignCode | None:
        campaign = await session.get(CampaignCode, campaign_id)
        if campaign is None:
            return None
        campaign.is_active = not campaign.is_active
        await session.commit()
        await session.refresh(campaign)
        return campaign

    @staticmethod
    async def delete(session, campaign_id: int) -> bool:
        campaign = await session.get(CampaignCode, campaign_id)
        if campaign is None:
            return False
        await session.delete(campaign)
        await session.commit()
        return True

    @staticmethod
    async def usage_count(session, campaign_id: int) -> int:
        from sqlalchemy import func as sqlfunc

        result = await session.execute(
            select(sqlfunc.count(CampaignCodeUsage.id)).where(
                CampaignCodeUsage.campaign_id == campaign_id
            )
        )
        return int(result.scalar_one() or 0)
