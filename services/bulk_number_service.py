"""
شراء الأرقام بالجملة.

المشكلة التي يحلها هذا الملف:
لا يوجد شراء بالجملة إطلاقاً — بحث `quantity|bulk|batch` في مسار
الأرقام كان صفراً. كل رقم = 4 نقرات، فعميل يريد 100 رقم يحتاج 400
نقرة. وهذا يطرد العملاء التجاريين الذين هم مصدر أغلب الإيراد.

الحل:
- طلب واحد بكمية حتى حد يضبطه الأدمن.
- تنفيذ متوازٍ بـ semaphore حتى لا نغرق المزودين.
- خصم تدريجي حسب الكمية (قائمة يضبطها الأدمن).
- استرجاع تلقائي لكل رقم فشل، فلا يُخصم المستخدم على ما لم يستلمه.
- تصدير النتائج.

لماذا لا يخسر المستخدم: الخصم الكلي يُحسب أولاً، ثم يُسترجع نصيب كل
رقم فشل. لو فشلت الدفعة كلها عاد المبلغ كاملاً.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select

from database.models import (
    Country,
    NumberOrder,
    NumberService,
    OrderStatus,
    TransactionType,
    User,
)
from providers.manager import provider_manager
from services.balance_service import BalanceService, InsufficientBalanceError
from services.feature_service import FeatureService
from services.pricing_service import PricingService

logger = logging.getLogger(__name__)


class BulkError(Exception):
    pass


class BulkNumberService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("bulk_numbers")

    @staticmethod
    async def max_quantity() -> int:
        return await FeatureService.config_int("bulk_numbers", "max_quantity", 500)

    @staticmethod
    async def concurrency() -> int:
        return max(1, await FeatureService.config_int("bulk_numbers", "concurrency", 10))

    @staticmethod
    async def discount_tiers() -> list[tuple[int, Decimal]]:
        """قائمة [الكمية، نسبة الخصم] مرتبة تصاعدياً."""
        raw = await FeatureService.config_json(
            "bulk_numbers", "discount_tiers_json", [[10, 1], [50, 3], [100, 5], [500, 8]]
        )
        tiers: list[tuple[int, Decimal]] = []
        for item in raw or []:
            try:
                qty, percent = int(item[0]), Decimal(str(item[1]))
            except (IndexError, TypeError, ValueError):
                continue
            if qty > 0 and percent >= 0:
                tiers.append((qty, percent))
        tiers.sort(key=lambda t: t[0])
        return tiers

    @staticmethod
    async def discount_percent(quantity: int) -> Decimal:
        """أعلى خصم يستحقه المستخدم حسب الكمية."""
        percent = Decimal("0")
        for qty, tier_percent in await BulkNumberService.discount_tiers():
            if quantity >= qty:
                percent = tier_percent
        return percent

    @staticmethod
    async def quote(
        session,
        service: NumberService,
        country: Country,
        quantity: int,
        strict_provider=None,
    ) -> dict:
        """يعرض التكلفة قبل التنفيذ حتى يوافق المستخدم على رقم واضح.

        ``strict_provider``: عند اختيار «سيرفر» محدد يحسب السعر من مزوده فقط.
        """
        if not await BulkNumberService.enabled():
            raise BulkError("الشراء بالجملة موقوف حالياً.")
        if quantity < 2:
            raise BulkError("الجملة تتطلب رقمين على الأقل.")
        limit = await BulkNumberService.max_quantity()
        if quantity > limit:
            raise BulkError(f"الحد الأقصى للدفعة الواحدة {limit} رقم.")

        if strict_provider is None:
            prices = await provider_manager.get_cheapest_price(service, country, session)
        else:
            prices = await provider_manager.get_cheapest_price(
                service, country, session, only_provider=strict_provider
            )
        if not prices:
            raise BulkError("لا توجد أرقام متاحة لهذه الخدمة والدولة.")

        cheapest = min(prices, key=prices.get)
        cost_usd = prices[cheapest]
        margin_type, margin_value = await PricingService.get_margin(
            session, service.code, country.code, cheapest
        )
        unit_price = PricingService.apply_margin(cost_usd, margin_type, margin_value)

        gross = (unit_price * Decimal(quantity)).quantize(Decimal("0.0001"))
        percent = await BulkNumberService.discount_percent(quantity)
        discount = (gross * percent / Decimal("100")).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )
        return {
            "quantity": quantity,
            "unit_price_usd": unit_price,
            "gross_usd": gross,
            "discount_percent": percent,
            "discount_usd": discount,
            "total_usd": (gross - discount).quantize(Decimal("0.0001")),
        }

    @staticmethod
    async def execute(
        session,
        user_id: int,
        service: NumberService,
        country: Country,
        quantity: int,
        timeout_minutes: int = 5,
        discount_percent: Decimal | None = None,
        strict_provider=None,
    ) -> dict:
        """
        ينفذ الدفعة. يرجع ملخصاً بالناجح والفاشل والمبالغ.

        ``discount_percent``: خصم إضافي على الإجمالي (مثل خصم الوكيل)
        يُطبق بعد خصم الجملة وقبل الخصم من الرصيد.
        ``strict_provider``: عند اختيار «سيرفر» محدد لا يشتري من غيره.
        """
        if strict_provider is None:
            estimate = await BulkNumberService.quote(session, service, country, quantity)
        else:
            estimate = await BulkNumberService.quote(
                session, service, country, quantity, strict_provider=strict_provider
            )
        total = estimate["total_usd"]
        if discount_percent is not None and discount_percent > 0:
            total = (
                total * (Decimal("100") - Decimal(str(discount_percent))) / Decimal("100")
            ).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        unit_price = estimate["unit_price_usd"]
        net_unit_price = (total / Decimal(quantity)).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )

        # ── الخصم المسبق لكامل الدفعة ──
        try:
            await BalanceService.deduct_balance(
                session,
                user_id,
                total,
                TransactionType.PURCHASE,
                description=f"شراء جملة {quantity} رقم {service.name_ar} - {country.name_ar}",
                is_purchase=True,
            )
        except InsufficientBalanceError as exc:
            raise BulkError(f"رصيدك غير كافٍ. المطلوب {total}$.") from exc

        # ── شراء متوازٍ بحد التزامن ──
        semaphore = asyncio.Semaphore(await BulkNumberService.concurrency())
        expires_at = datetime.utcnow() + timedelta(minutes=max(1, timeout_minutes))

        async def _buy_one(index: int):
            async with semaphore:
                try:
                    # لا نمرر session هنا: SQLAlchemy AsyncSession غير آمنة
                    # للاستخدام المتوازي. فحص حالة المزودين يتم في quote()،
                    # أما عمليات الشبكة نفسها فتعمل بلا جلسة مشتركة.
                    if strict_provider is None:
                        return index, await provider_manager.buy_number(service, country)
                    return index, await provider_manager.buy_number(
                        service, country, strict_provider=strict_provider
                    )
                except Exception as exc:  # noqa: BLE001 - فشل رقم لا يسقط الدفعة
                    logger.debug("فشل شراء رقم %s في الدفعة: %s", index, exc)
                    return index, None

        results = await asyncio.gather(*(_buy_one(i) for i in range(quantity)))

        succeeded: list[NumberOrder] = []
        failed = 0
        for index, buy_result in results:
            if buy_result is None:
                failed += 1
                continue
            order = NumberOrder(
                user_id=user_id,
                provider=buy_result.provider,
                provider_order_id=buy_result.provider_order_id,
                service=service.code,
                country_code=country.code,
                phone_number=buy_result.phone_number,
                price_provider_usd=buy_result.cost_usd,
                price_sell_usd=net_unit_price,
                status=OrderStatus.PENDING,
                expires_at=expires_at,
            )
            session.add(order)
            succeeded.append(order)
        await session.commit()

        # ── استرجاع نصيب ما فشل ──
        # مهم: الاسترجاع يجب أن يكون من السعر الصافي بعد خصم الجملة، لا من
        # unit_price قبل الخصم، وإلا قد يسترجع المستخدم أكثر مما دُفع فعلياً.
        refund = Decimal("0")
        if failed:
            refund = (net_unit_price * Decimal(failed)).quantize(
                Decimal("0.0001"), rounding=ROUND_DOWN
            )
            await BalanceService.add_balance(
                session,
                user_id,
                refund,
                TransactionType.REFUND,
                description=f"استرجاع {failed} رقم فشل في دفعة جملة",
            )

        await FeatureService.track(
            "bulk_numbers", "executed", user_id=user_id, value=str(len(succeeded))
        )

        return {
            "requested": quantity,
            "succeeded": len(succeeded),
            "failed": failed,
            "total_charged_usd": total,
            "refunded_usd": refund,
            "net_charged_usd": (total - refund).quantize(Decimal("0.0001")),
            "orders": succeeded,
        }

    @staticmethod
    def export_csv(orders: list[NumberOrder]) -> str:
        """تصدير النتائج لملف CSV يبتلعه أي نظام أتمتة."""
        lines = ["order_id,phone_number,provider,status,created_at"]
        for order in orders:
            lines.append(
                f"{order.id},{order.phone_number},"
                f"{getattr(order.provider, 'value', order.provider)},"
                f"{getattr(order.status, 'value', order.status)},"
                f"{datetime.utcnow().isoformat()}"
            )
        return "\n".join(lines)
