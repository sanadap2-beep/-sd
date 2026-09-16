"""
خدمة إنذار انخفاض السعر.

المستخدم يثبّت تنبيه (خدمة + دولة + سعر مستهدف) من البوت، وتفحص
الخدمة دورياً (أو بعد أي تحديث) الأسعار الفعلية من نفس مصدر
الأسعار المعروض للشراء، وتُرسل إشعاراً فور النزول تحت الهدف.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select

from database.models import PriceAlert
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class PriceAlertError(Exception):
    """خطأ واضح في إنذارات السعر."""


class PriceAlertService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("price_alerts")

    @staticmethod
    async def user_alerts(session, user_id: int) -> list[PriceAlert]:
        result = await session.execute(
            select(PriceAlert)
            .where(PriceAlert.user_id == user_id)
            .order_by(PriceAlert.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def active_alerts(session) -> list[PriceAlert]:
        result = await session.execute(
            select(PriceAlert)
            .where(PriceAlert.status == "active")
            .order_by(PriceAlert.created_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def add_alert(
        session,
        user_id: int,
        service_code: str,
        country_code: str,
        target_price_usd: Decimal,
    ) -> PriceAlert:
        limit = await FeatureService.config("price_alerts", "max_alerts_per_user", 10)
        existing = await PriceAlertService.user_alerts(session, user_id)
        if len(existing) >= int(limit):
            raise PriceAlertError(
                f"وصلت للحد الأقصى ({limit}) تنبيهات. احذف تنبيهاً لتضيف جديداً."
            )
        alert = PriceAlert(
            user_id=user_id,
            service_code=service_code,
            country_code=country_code,
            target_price_usd=target_price_usd,
            status="active",
        )
        session.add(alert)
        await session.commit()
        await session.refresh(alert)
        return alert

    @staticmethod
    async def delete_alert(session, alert_id: int, user_id: int) -> bool:
        result = await session.execute(
            delete(PriceAlert).where(PriceAlert.id == alert_id, PriceAlert.user_id == user_id)
        )
        await session.commit()
        return result.rowcount > 0

    @staticmethod
    async def pause_alert(session, alert_id: int, user_id: int) -> bool:
        alert = await session.get(PriceAlert, alert_id)
        if alert is None or alert.user_id != user_id:
            return False
        alert.status = "paused" if alert.status == "active" else "active"
        await session.commit()
        return True

    @staticmethod
    async def check_price(session, alert_id: int, current_price: Decimal) -> bool:
        """
        افحص سعراً حالياً مقابل التنبيه. إذا تحقق (سعر <= الهدف)،
        عطّل التفعيل وأعد الهدف الفعلي. تُرجع True عندما يتحقق التنبيه.
        """
        alert = await session.get(PriceAlert, alert_id)
        if alert is None or alert.status != "active":
            return False
        if current_price is None or current_price <= Decimal("0"):
            return False
        alert.last_checked_price = current_price
        if current_price <= alert.target_price_usd:
            alert.status = "triggered"
            alert.last_triggered_at = datetime.utcnow()
        await session.commit()
        return alert.status == "triggered"