"""
حساب سعر البيع النهائي = تكلفة المزود + نسبة/مبلغ ربح.
العملة: دولار أمريكي (USD) بالكامل.
منطق الأولوية عند البحث عن نسبة مخصصة:
(خدمة+دولة+مزود) > (خدمة+دولة) > (خدمة+مزود) > (خدمة فقط) > النسبة العامة.
"""

from decimal import Decimal, ROUND_UP

from sqlalchemy import select

from database.models import ServicePricing, ProviderName
from services.settings_service import SettingsService


def _manual_cost_key(service: str, country_code: str) -> str:
    """مفتاح تخزين سعر التكلفة اليدوي في جدول الإعدادات."""
    return f"manual_cost:{service}:{country_code}"


class PricingService:
    # ══════════════ التكلفة اليدوية ══════════════

    @staticmethod
    async def get_manual_cost(
        session,
        service: str,
        country_code: str,
    ) -> Decimal | None:
        """سعر التكلفة اليدوي الذي حدده الأدمن (إن وُجد).

        إن حُدد فهو يتجاوز الأسعار الحية من المزود تماماً: لوحة
        الدول وصفحة الشراء تعرضانه مباشرة دون أي طلب شبكي.
        """
        value = await SettingsService.get(_manual_cost_key(service, country_code))
        if value is None:
            return None
        try:
            cost = Decimal(value)
        except Exception:  # noqa: BLE001 - قيمة تالفة = لا تسعير يدوي
            return None
        return cost if cost > 0 else None

    @staticmethod
    async def set_manual_cost(
        session,
        service: str,
        country_code: str,
        cost_usd: Decimal,
    ) -> None:
        """يحفظ سعر تكلفة يدوياً لخدمة/دولة."""
        await SettingsService.set(
            session, _manual_cost_key(service, country_code), str(cost_usd)
        )

    @staticmethod
    async def delete_manual_cost(session, service: str, country_code: str) -> None:
        """يزيل التسعير اليدوي فتعود الخدمة/الدولة للأسعار الحية."""
        await SettingsService.delete(
            session, _manual_cost_key(service, country_code)
        )

    # ══════════════ هوامش الربح ══════════════

    @staticmethod
    async def get_margin(
        session,
        service: str,
        country_code: str,
        provider: ProviderName,
    ) -> tuple[str, Decimal]:
        """
        يجلب نسبة الربح المناسبة حسب الأولوية.
        يرجع (margin_type, margin_value).
        """
        result = await session.execute(
            select(ServicePricing).where(ServicePricing.service == service)
        )
        rows = result.scalars().all()

        best = None
        best_score = -1

        for row in rows:
            score = 0

            if row.country_code is not None:
                if row.country_code != country_code:
                    continue
                score += 2

            if row.provider is not None:
                if row.provider != provider:
                    continue
                score += 1

            if score > best_score:
                best_score = score
                best = row

        if best:
            return best.margin_type, best.margin_value

        default_margin = await SettingsService.get_decimal(
            "default_profit_margin_percent", Decimal("50")
        )
        return "percent", default_margin

    @staticmethod
    def apply_margin(
        cost_usd: Decimal,
        margin_type: str,
        margin_value: Decimal,
    ) -> Decimal:
        """
        يطبق نسبة الربح على سعر التكلفة بالدولار.
        margin_type: percent → نسبة مئوية
        margin_type: fixed   → مبلغ ثابت
        """
        if margin_type == "percent":
            sell = cost_usd * (Decimal("1") + margin_value / Decimal("100"))
        else:
            sell = cost_usd + margin_value

        return sell.quantize(Decimal("0.0001"), rounding=ROUND_UP)

    @staticmethod
    async def calculate_sell_price(
        session,
        service: str,
        country_code: str,
        provider: ProviderName,
        cost_usd: Decimal,
    ) -> Decimal:
        """
        دالة مساعدة تجمع get_margin و apply_margin في خطوة واحدة.
        """
        margin_type, margin_value = await PricingService.get_margin(
            session, service, country_code, provider
        )
        return PricingService.apply_margin(cost_usd, margin_type, margin_value)

    @staticmethod
    async def set_custom_margin(
        session,
        service: str,
        margin_type: str,
        margin_value: Decimal,
        country_code: str | None = None,
        provider: ProviderName | None = None,
    ) -> ServicePricing:
        """
        يضيف أو يعدل نسبة ربح مخصصة لخدمة/دولة/مزود معين.
        """
        result = await session.execute(
            select(ServicePricing).where(
                ServicePricing.service == service,
                ServicePricing.country_code == country_code,
                ServicePricing.provider == provider,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.margin_type = margin_type
            existing.margin_value = margin_value
            await session.commit()
            await session.refresh(existing)
            return existing

        pricing = ServicePricing(
            service=service,
            country_code=country_code,
            provider=provider,
            margin_type=margin_type,
            margin_value=margin_value,
        )
        session.add(pricing)
        await session.commit()
        await session.refresh(pricing)
        return pricing

    @staticmethod
    async def delete_custom_margin(session, pricing_id: int) -> bool:
        pricing = await session.get(ServicePricing, pricing_id)
        if pricing is None:
            return False
        await session.delete(pricing)
        await session.commit()
        return True

    @staticmethod
    async def get_all_custom_margins(
        session,
    ) -> list[ServicePricing]:
        result = await session.execute(select(ServicePricing))
        return list(result.scalars().all())
