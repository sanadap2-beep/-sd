"""
الأرقام المقيمة + الاشتراك العائلي المقسّم + البركة المُسخَّنة.

ثلاث ميزات تحوّل نموذج الإيراد:

1) الأرقام المقيمة (Dedicated Numbers)
   بدل بيع الرقم مرة واحدة بـ0.5$، يُستأجر لفترة يظل فيها ملك
   المستخدم وتُحوَّل إليه كل رسائله. هذا إيراد شهري متكرر بدل
   إيراد لمرة واحدة.

2) الاشتراك العائلي المقسّم (Shared Plans)
   خطة متعددة المقاعد بتقسيم شهري. المقعد الشاغر يمتلئ تلقائياً من
   قائمة انتظار، فلا تسرّب في الإيراد.

3) البركة المُسخَّنة (Warm Pool)
   أرقام مشترى مسبقاً لأكثر الخدمات طلباً، فتسليم الطلب يصير فورياً
   بدل انتظار دورة شراء كاملة.

كلها قابلة للإيقاف والضبط من مركز الإضافات.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from database.models import (
    Country,
    NumberOrder,
    NumberService,
    OrderStatus,
    TransactionType,
    User,
)
from providers.manager import ProviderUnavailableError, provider_manager
from services.balance_service import BalanceService, InsufficientBalanceError
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class DedicatedNumberError(Exception):
    pass


class DedicatedNumberService:
    """رقم يبقى ملك المستخدم طوال فترة الاستئجار."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("dedicated_numbers")

    @staticmethod
    async def plans() -> list[dict]:
        raw = await FeatureService.config_json(
            "dedicated_numbers",
            "plans_json",
            [{"days": 30, "price_usd": 3.0}, {"days": 90, "price_usd": 8.0},
             {"days": 365, "price_usd": 25.0}],
        )
        plans = []
        for item in raw or []:
            try:
                days = int(item["days"])
                price = Decimal(str(item["price_usd"]))
            except (KeyError, TypeError, ValueError, ArithmeticError):
                continue
            if days > 0 and price > 0:
                plans.append({"days": days, "price_usd": price})
        plans.sort(key=lambda p: p["days"])
        return plans

    @staticmethod
    async def rent(
        session,
        user_id: int,
        service: NumberService,
        country: Country,
        days: int,
    ) -> dict:
        """يستأجر رقماً لفترة محددة."""
        if not await DedicatedNumberService.enabled():
            raise DedicatedNumberError("الأرقام المقيمة موقوفة حالياً.")

        plan = next((p for p in await DedicatedNumberService.plans() if p["days"] == days), None)
        if plan is None:
            raise DedicatedNumberError("مدة الاستئجار غير متاحة.")

        try:
            await BalanceService.deduct_balance(
                session,
                user_id,
                plan["price_usd"],
                TransactionType.PURCHASE,
                description=f"استئجار رقم مقيم {days} يوم - {service.name_ar} {country.name_ar}",
                is_purchase=True,
            )
        except InsufficientBalanceError as exc:
            raise DedicatedNumberError(
                f"رصيدك غير كافٍ. المطلوب {plan['price_usd']}$."
            ) from exc

        try:
            bought = await provider_manager.buy_number(service, country, session)
        except (ProviderUnavailableError, Exception) as exc:  # noqa: BLE001
            await BalanceService.add_balance(
                session, user_id, plan["price_usd"], TransactionType.REFUND,
                description="استرجاع - تعذّر توفير رقم مقيم",
            )
            raise DedicatedNumberError("لا أرقام متاحة حالياً. استُرجع رصيدك.") from exc

        expires_at = datetime.utcnow() + timedelta(days=days)
        order = NumberOrder(
            user_id=user_id,
            provider=bought.provider,
            provider_order_id=bought.provider_order_id,
            service=service.code,
            country_code=country.code,
            phone_number=bought.phone_number,
            price_provider_usd=bought.cost_usd,
            price_sell_usd=plan["price_usd"],
            status=OrderStatus.PENDING,
            expires_at=expires_at,
            dedicated_until=expires_at,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        await FeatureService.track(
            "dedicated_numbers", "rented", user_id=user_id, value=f"{days}d"
        )
        return {
            "order_id": order.id,
            "phone_number": order.phone_number,
            "days": days,
            "price_usd": plan["price_usd"],
            "expires_at": expires_at,
        }

    @staticmethod
    async def active_for(session, user_id: int) -> list[NumberOrder]:
        now = datetime.utcnow()
        result = await session.execute(
            select(NumberOrder).where(
                NumberOrder.user_id == user_id,
                NumberOrder.dedicated_until.is_not(None),
                NumberOrder.dedicated_until > now,
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def mrr(session) -> Decimal:
        """الإيراد الشهري المتوقع من الأرقام المقيمة."""
        now = datetime.utcnow()
        horizon = now + timedelta(days=30)
        result = await session.execute(
            select(NumberOrder).where(
                NumberOrder.dedicated_until.is_not(None),
                NumberOrder.dedicated_until > now,
            )
        )
        total = Decimal("0")
        for order in result.scalars().all():
            if order.dedicated_until and order.dedicated_until <= horizon:
                total += Decimal(str(order.price_sell_usd or 0))
        return total


class SharedPlanService:
    """خطة متعددة المقاعد بتقسيم شهري وملء تلقائي للشواغر."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("shared_plans")

    @staticmethod
    async def default_seats() -> int:
        return max(1, await FeatureService.config_int("shared_plans", "default_seats", 6))

    @staticmethod
    def split_cost(total_usd: Decimal, seats: int) -> Decimal:
        """نصيب المقعد، مقرباً لأعلى حتى لا تخسر المنصة من التقريب."""
        if seats <= 0:
            return total_usd
        per = total_usd / Decimal(seats)
        return per.quantize(Decimal("0.0001"))

    @staticmethod
    async def fill_vacant(session, group_id: int, candidates: list[int]) -> list[int]:
        """يملأ المقاعد الشاغرة من قائمة الانتظار بالترتيب."""
        if not await SharedPlanService.enabled():
            return []
        if not await FeatureService.config_bool("shared_plans", "waitlist_enabled", True):
            return []
        filled = []
        for user_id in candidates:
            user = await session.get(User, user_id)
            if user is None or user.is_banned:
                continue
            filled.append(user_id)
        if filled:
            await FeatureService.track(
                "shared_plans", "filled", value=f"{group_id}:{len(filled)}"
            )
        return filled


class WarmPoolService:
    """
    أرقام مُشترى مسبقاً لتسليم فوري.

    جدول مستقل (WarmPoolNumber) لأن `number_orders.user_id` غير قابل
    للفراغ، والرقم في البركة لا مالك له بعد. عند الإسناد يُنشأ
    NumberOrder حقيقي ويُحذف الصف من البركة.
    """

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("warm_pool")

    @staticmethod
    async def pool_size() -> int:
        return max(0, await FeatureService.config_int("warm_pool", "pool_size", 20))

    @staticmethod
    async def refill_threshold() -> int:
        return max(0, await FeatureService.config_int("warm_pool", "refill_threshold", 5))

    @staticmethod
    async def _idle_minutes() -> int:
        return max(1, await FeatureService.config_int("warm_pool", "max_idle_minutes", 20))

    @staticmethod
    async def level(session, service_code: str, country_code: str) -> int:
        """كم رقماً جاهزاً في البركة لهذه الخدمة والدولة."""
        from database.models import WarmPoolNumber

        now = datetime.utcnow()
        count = (
            await session.execute(
                select(func.count(WarmPoolNumber.id)).where(
                    WarmPoolNumber.service_code == service_code,
                    WarmPoolNumber.country_code == country_code,
                    WarmPoolNumber.expires_at > now,
                )
            )
        ).scalar_one()
        return int(count or 0)

    @staticmethod
    async def needs_refill(session, service_code: str, country_code: str) -> bool:
        return await WarmPoolService.level(session, service_code, country_code) < (
            await WarmPoolService.refill_threshold()
        )

    @staticmethod
    async def available(session, service_code: str, country_code: str):
        """يسحب رقماً جاهزاً من البركة إن وُجد."""
        from database.models import WarmPoolNumber

        if not await WarmPoolService.enabled():
            return None
        now = datetime.utcnow()
        result = await session.execute(
            select(WarmPoolNumber)
            .where(
                WarmPoolNumber.service_code == service_code,
                WarmPoolNumber.country_code == country_code,
                WarmPoolNumber.expires_at > now,
            )
            .order_by(WarmPoolNumber.id)
            .limit(1)
        )
        return result.scalars().first()

    @staticmethod
    async def assign(session, pooled, user_id: int, timeout_minutes: int):
        """
        يسند رقماً من البركة لمستخدم بدل شراء جديد.
        يُنشئ NumberOrder حقيقياً ويحذف الصف من البركة في نفس المعاملة.
        """
        from database.models import OrderStatus, WarmPoolNumber

        order = NumberOrder(
            user_id=user_id,
            provider=pooled.provider,
            provider_order_id=pooled.provider_order_id,
            service=pooled.service_code,
            country_code=pooled.country_code,
            phone_number=pooled.phone_number,
            price_provider_usd=pooled.cost_usd,
            price_sell_usd=Decimal("0"),
            status=OrderStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(minutes=max(1, timeout_minutes)),
        )
        session.add(order)
        await session.delete(pooled)
        await session.commit()
        await FeatureService.track("warm_pool", "assigned", user_id=user_id)
        return order

    @staticmethod
    async def warm_up(
        session,
        service: NumberService,
        country: Country,
        target: int,
        cost_budget_usd: Decimal,
    ) -> dict:
        """يشتري أرقاماً للبركة ضمن ميزانية يحددها الأدمن."""
        from database.models import WarmPoolNumber

        if not await WarmPoolService.enabled():
            return {"added": 0, "spent_usd": Decimal("0")}

        current = await WarmPoolService.level(session, service.code, country.code)
        needed = max(0, min(target, await WarmPoolService.pool_size()) - current)
        if needed <= 0:
            return {"added": 0, "spent_usd": Decimal("0")}

        prices = await provider_manager.get_cheapest_price(service, country, session)
        if not prices:
            return {"added": 0, "spent_usd": Decimal("0")}
        unit_cost = min(prices.values())

        # لا نتجاوز الميزانية حتى لو كان المطلوب أكثر
        affordable = int(cost_budget_usd / unit_cost) if unit_cost > 0 else 0
        needed = min(needed, max(0, affordable))

        idle = await WarmPoolService._idle_minutes()
        added = 0
        spent = Decimal("0")
        for _ in range(needed):
            try:
                bought = await provider_manager.buy_number(service, country, session)
            except (ProviderUnavailableError, Exception) as exc:  # noqa: BLE001
                logger.debug("تعذّر تسخين البركة: %s", exc)
                break
            session.add(
                WarmPoolNumber(
                    provider=bought.provider,
                    provider_order_id=bought.provider_order_id,
                    phone_number=bought.phone_number,
                    service_code=service.code,
                    country_code=country.code,
                    cost_usd=bought.cost_usd,
                    expires_at=datetime.utcnow() + timedelta(minutes=idle),
                )
            )
            added += 1
            spent += bought.cost_usd
        await session.commit()

        if added:
            await FeatureService.track(
                "warm_pool", "warmed", value=f"{service.code}:{country.code}:{added}"
            )
        return {"added": added, "spent_usd": spent}

    @staticmethod
    async def sweep_expired(session) -> int:
        """يحذف أرقام البركة المنتهية حتى لا تُسند أرقام ميتة."""
        from database.models import WarmPoolNumber

        result = await session.execute(
            select(WarmPoolNumber).where(WarmPoolNumber.expires_at <= datetime.utcnow())
        )
        count = 0
        for row in result.scalars().all():
            await session.delete(row)
            count += 1
        if count:
            await session.commit()
        return count
