"""
بورصة الأرقام + قابلية النقل + الإسقاطات النادرة + شهادات VIP.

أربع ميزات تحوّل الرقم من سلعة استهلاكية إلى أصل:

1) بورصة الأرقام: أسعار حية وأوامر حدّ تُنفَّذ تلقائياً عند سعر محدد.
   المستخدم يعود ليراقب السوق، وهذا أعلى احتفاظ ممكن.

2) قابلية النقل: الرقم نفسه يُعاد تأجيره لنفس المستخدم لاحقاً، فلا
   يفقد خطه الذي سجله في كل خدماته.

3) الإسقاطات النادرة: أرقام بنمط مميز بكميات محدودة مع تنبيه لحظي.
   FOMO حقيقي بدل عروض باردة.

4) شهادات VIP: ملكية دائمة موثقة لرقم نادر قابلة للتحويل.

كلها قابلة للإيقاف والضبط من مركز الإضافات.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import desc, func, select

from database.models import (
    MarketListing,
    MarketListingKind,
    MarketListingStatus,
    NumberOrder,
    OrderStatus,
    TransactionType,
    User,
)
from services.balance_service import BalanceService
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class ExchangeError(Exception):
    pass


class NumberExchangeService:
    """أوامر حدّ على أسعار الأرقام."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("number_exchange")

    @staticmethod
    async def place_limit_order(
        session,
        user_id: int,
        service_code: str,
        country_code: str,
        quantity: int,
        target_price_usd: Decimal,
    ) -> dict:
        """«اشترِ تلقائياً حين ينزل السعر تحت X»."""
        if not await NumberExchangeService.enabled():
            raise ExchangeError("بورصة الأرقام موقوفة حالياً.")
        if quantity <= 0:
            raise ExchangeError("الكمية يجب أن تكون موجبة.")
        if target_price_usd <= 0:
            raise ExchangeError("السعر المستهدف يجب أن يكون موجباً.")

        limit = await FeatureService.config_int("number_exchange", "max_open_orders_per_user", 20)
        from database.models import PriceLimitOrder

        existing = (
            await session.execute(
                select(func.count(PriceLimitOrder.id)).where(
                    PriceLimitOrder.user_id == user_id,
                    PriceLimitOrder.status == "open",
                )
            )
        ).scalar_one()
        if limit > 0 and int(existing) >= limit:
            raise ExchangeError(f"لديك {limit} أوامر مفتوحة كحد أقصى.")

        order = PriceLimitOrder(
            user_id=user_id,
            service_code=service_code,
            country_code=country_code,
            quantity=quantity,
            target_price_usd=target_price_usd,
            status="open",
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        await FeatureService.track("number_exchange", "order_placed", user_id=user_id)
        return {
            "order_id": order.id,
            "target_price_usd": target_price_usd,
            "quantity": quantity,
        }

    @staticmethod
    async def match(session, service_code: str, country_code: str, current_price: Decimal) -> list[dict]:
        """ينفذ الأوامر التي تحقق شرطها عند السعر الحالي."""
        if not await NumberExchangeService.enabled():
            return []
        from database.models import PriceLimitOrder

        result = await session.execute(
            select(PriceLimitOrder).where(
                PriceLimitOrder.service_code == service_code,
                PriceLimitOrder.country_code == country_code,
                PriceLimitOrder.status == "open",
                PriceLimitOrder.target_price_usd >= current_price,
            )
        )
        executed = []
        for order in result.scalars().all():
            order.status = "triggered"
            order.triggered_at = datetime.utcnow()
            order.triggered_price_usd = current_price
            executed.append(
                {
                    "order_id": order.id,
                    "user_id": order.user_id,
                    "quantity": order.quantity,
                    "price_usd": current_price,
                }
            )
        if executed:
            await session.commit()
            await FeatureService.track(
                "number_exchange", "matched", value=f"{service_code}:{len(executed)}"
            )
        return executed

    @staticmethod
    async def cancel(session, order_id: int, user_id: int) -> bool:
        from database.models import PriceLimitOrder

        order = await session.get(PriceLimitOrder, order_id)
        if order is None or order.user_id != user_id or order.status != "open":
            return False
        order.status = "cancelled"
        await session.commit()
        return True

    @staticmethod
    async def open_orders(session, user_id: int) -> list:
        from database.models import PriceLimitOrder

        result = await session.execute(
            select(PriceLimitOrder).where(
                PriceLimitOrder.user_id == user_id,
                PriceLimitOrder.status == "open",
            )
        )
        return list(result.scalars().all())


class NumberPortabilityService:
    """الرقم نفسه يعود لنفس المستخدم بدل أن يُفقد."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("number_portability")

    @staticmethod
    async def reserve_hours() -> int:
        return max(1, await FeatureService.config_int("number_portability", "reserve_hours", 72))

    @staticmethod
    async def reserve(session, order_id: int, user_id: int) -> dict | None:
        """يحجز الرقم لمالكه فترة حتى يستطيع استعادته."""
        if not await NumberPortabilityService.enabled():
            return None
        order = await session.get(NumberOrder, order_id)
        if order is None or order.user_id != user_id:
            return None
        if not order.phone_number:
            return None
        until = datetime.utcnow() + timedelta(hours=await NumberPortabilityService.reserve_hours())
        order.portable_until = until
        await session.commit()
        await FeatureService.track("number_portability", "reserved", user_id=user_id)
        return {"phone_number": order.phone_number, "reserved_until": until}

    @staticmethod
    async def reclaimable(session, user_id: int) -> list[NumberOrder]:
        """أرقام المستخدم ما زالت محفوظة له ويمكن استعادتها."""
        result = await session.execute(
            select(NumberOrder).where(
                NumberOrder.user_id == user_id,
                NumberOrder.portable_until.is_not(None),
                NumberOrder.portable_until > datetime.utcnow(),
            )
        )
        return list(result.scalars().all())


class RareDropService:
    """إسقاطات أرقام نادرة بكميات محدودة."""

    # أنماط نادرة فعلاً. الانتباه: النمط الساذج `(\d+)\1+` يطابق أي رقم
    # مكرر ("55") فيصير كل رقم تقريباً «نادراً». لذلك التكرار هنا يتطلب
    # كتلة من 3 أرقام على الأقل.
    DEFAULT_PATTERNS = (
        r"(.)\1{3,}",           # أربعة متطابقة متتالية: 1111
        r"(\d)(\d)\1\2",        # نمط متناوب: 1212
        r"(\d{3,})\1+",         # كتلة معادة: 123123
        r"(\d)(\d)(\d)\1\2\3",  # ABCABC
    )

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("rare_number_drops")

    @staticmethod
    async def patterns() -> list[re.Pattern]:
        raw = await FeatureService.config_json(
            "rare_number_drops", "pattern_rules_json", list(RareDropService.DEFAULT_PATTERNS)
        )
        compiled = []
        for item in raw or []:
            try:
                compiled.append(re.compile(str(item)))
            except re.error:
                continue
        return compiled or [re.compile(p) for p in RareDropService.DEFAULT_PATTERNS]

    @classmethod
    async def is_rare(cls, phone_number: str) -> bool:
        """هل الرقم يطابق نمطاً مميزاً؟"""
        if not await RareDropService.enabled():
            return False
        digits = re.sub(r"\D", "", phone_number or "")
        if len(digits) < 4:
            return False
        return any(pattern.search(digits) for pattern in await cls.patterns())

    @staticmethod
    async def drop_config() -> dict:
        return {
            "interval_hours": await FeatureService.config_int(
                "rare_number_drops", "drop_interval_hours", 6
            ),
            "quantity": await FeatureService.config_int(
                "rare_number_drops", "quantity_per_drop", 5
            ),
        }


class VipCertificateService:
    """ملكية دائمة موثقة لرقم نادر، قابلة للتحويل."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("vip_number_certificates")

    @staticmethod
    async def fee() -> Decimal:
        return Decimal(str(await FeatureService.config_decimal(
            "vip_number_certificates", "certificate_fee_usd", 10.0
        )))

    @staticmethod
    async def issue(session, user_id: int, phone_number: str) -> dict:
        """يصدر شهادة ملكية بعد دفع الرسم."""
        if not await VipCertificateService.enabled():
            raise ExchangeError("شهادات VIP موقوفة حالياً.")
        if not await RareDropService.is_rare(phone_number):
            raise ExchangeError("هذا الرقم ليس نادراً بما يكفي لشهادة VIP.")

        from database.models import VipCertificate

        existing = (
            await session.execute(
                select(VipCertificate).where(VipCertificate.phone_number == phone_number)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ExchangeError("لهذا الرقم شهادة صادرة مسبقاً.")

        fee = await VipCertificateService.fee()
        await BalanceService.deduct_balance(
            session, user_id, fee, TransactionType.PURCHASE,
            description=f"إصدار شهادة VIP للرقم {phone_number}", is_purchase=True,
        )

        certificate = VipCertificate(
            owner_id=user_id,
            phone_number=phone_number,
            serial=f"VIP-{user_id}-{int(datetime.utcnow().timestamp())}",
            issued_at=datetime.utcnow(),
        )
        session.add(certificate)
        await session.commit()
        await session.refresh(certificate)
        await FeatureService.track("vip_number_certificates", "issued", user_id=user_id)
        return {"serial": certificate.serial, "phone_number": phone_number, "fee_usd": fee}

    @staticmethod
    async def transfer(session, serial: str, from_user_id: int, to_user_id: int) -> bool:
        """ينقل الملكية — وهذا ما يجعلها أصلاً لا مجرد رقم."""
        from database.models import VipCertificate

        result = await session.execute(
            select(VipCertificate).where(VipCertificate.serial == serial)
        )
        certificate = result.scalar_one_or_none()
        if certificate is None or certificate.owner_id != from_user_id:
            return False
        if certificate.revoked:
            return False
        certificate.owner_id = to_user_id
        certificate.transferred_at = datetime.utcnow()
        await session.commit()
        await FeatureService.track(
            "vip_number_certificates", "transferred", user_id=to_user_id, value=serial
        )
        return True

    @staticmethod
    async def owned_by(session, user_id: int) -> list:
        from database.models import VipCertificate

        result = await session.execute(
            select(VipCertificate).where(
                VipCertificate.owner_id == user_id,
                VipCertificate.revoked.is_(False),
            )
        )
        return list(result.scalars().all())
