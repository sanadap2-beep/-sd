"""
إشعار انتهاء العروض الخاصة.

قبل نهاية العرض يذكر هذا الفحص لمن شاهد العرض أن الفرصة تنتهي —
يحدد الأدمن المدة بالأمام (افتراضياً ساعة) والهدف (كل من شاهد
العرض Once فقط لتجنب الإزعاج).

الفحص يستدعى دورياً من مهمة المجدول خلال اليوم.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from database.models import SpecialOffer
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class OfferExpiryService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("offer_expiry_alerts")

    @staticmethod
    async def offers_expiring_soon(session, minutes_before: int | None = None) -> list[dict]:
        """
        يحتاج عرضاً نشطاً سينتهي خلال minutes_before من الآن —
        ولم يُرسل تذكيره بعد (بعلامة reminder_4h/2h).
        """
        if not await OfferExpiryService.enabled():
            return []
        if minutes_before is None:
            minutes_before = int(await FeatureService.config("offer_expiry_alerts", "remind_minutes_before", 60))
        now = datetime.utcnow()
        window_end = now + timedelta(minutes=minutes_before)

        result = await session.execute(
            select(SpecialOffer).where(
                SpecialOffer.status == "active",
                SpecialOffer.ends_at.is_not(None),
                SpecialOffer.starts_at.is_not(None),
            )
        )
        due = []
        for offer in result.scalars().all():
            if offer.ends_at is None:
                continue
            if now < offer.ends_at <= window_end:
                # نمنع التكرار لأقرب 3 ساعات متبقية ضمن نفس النافذة.
                if issue_prevents_resend(offer, now, offer.ends_at):
                    continue
                due.append(
                    {
                        "offer": offer,
                        "minutes_left": max(1, int((offer.ends_at - now).total_seconds() // 60)),
                    }
                )
        return due

    @staticmethod
    async def mark_reminded(session, offer_id: int, close_to_end: bool) -> None:
        offer = await session.get(SpecialOffer, offer_id)
        if offer is None:
            return
        if close_to_end:
            offer.reminder_2h_sent = True
        else:
            offer.reminder_4h_sent = True
        await session.commit()


def issue_prevents_resend(offer, now: datetime, ends_at: datetime) -> bool:
    """
    حارس بسيط: يعيد نفس التذكير مرة واحدة فقط لكل عرض في آخر 3 ساعات.
    للتكرار الآمن عبر النوافذ دون تضخم الرسائل.
    """
    minutes_left = (ends_at - now).total_seconds() // 60
    if minutes_left <= 180 and offer.reminder_2h_sent:
        return True
    if minutes_left > 180 and offer.reminder_4h_sent:
        return True
    return False