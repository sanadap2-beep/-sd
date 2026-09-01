"""اختبارات التسعير اليدوي للدول.

تتحقق من:
1) حفظ/قراءة/حذف التكلفة اليدوية (PricingService).
2) تجاوز التكلفة اليدوية للأسعار الحية في ProviderManager.
3) ظهور الدول المُسعّرة يدوياً في لوحة المستخدم حتى بلا مزود حي.
4) حساب سعر البيع = تكلفة يدوية + هامش.
5) تنظيف الأسعار اليدوية عند تصفير الدول المسحوبة.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import Country, NumberService, ProviderName
from services.pricing_service import PricingService
from services.settings_service import SettingsService
from services.number_catalog_service import build_board, invalidate_board


async def _seed_manual_pricing_db():
    """دولة مفعّلة مع خدمة واتساب، ومدير وهمي بلا أي أسعار حية."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(NumberService).where(NumberService.code == "whatsapp")
        )
        service = result.scalar_one()
        service.herosms_code = "wa"
        session.add(
            Country(
                code="indonesia",
                name_ar="إندونيسيا",
                flag="🇮🇩",
                herosms_code="6",
                is_active=True,
            )
        )
        await session.commit()
        return service


class _DeadLiveManager:
    """مدير بلا أسعار حية إطلاقاً — يحاكي مزوداً معطلاً/متقطعاً.

    يدعم manual_prices_for مثل المدير الحقيقي حتى تعمل اللوحة
    بالتسعير اليدوي، ويرفض المسار الحي (يرجع قاموساً فارغاً).
    """

    def __init__(self):
        self.live_calls = 0

    def manual_prices_for(self, service, country, cost):
        # مزود herosms مؤهل: للدولة والخدمة أكواد HeroSMS
        return {ProviderName.HEROSMS: cost}

    async def get_cheapest_price(self, service, country, session=None):
        self.live_calls += 1
        return {}


@pytest.mark.asyncio
async def test_manual_cost_store_read_delete():
    SettingsService._cache.clear()
    await _seed_manual_pricing_db()

    async with async_session_maker() as session:
        # لا تكلفة يدوية بعد
        assert await PricingService.get_manual_cost(session, "whatsapp", "indonesia") is None

        # حفظ ثم قراءة
        await PricingService.set_manual_cost(
            session, "whatsapp", "indonesia", Decimal("0.5")
        )
        assert await PricingService.get_manual_cost(session, "whatsapp", "indonesia") == Decimal("0.5")

        # الحذف يعيد التسعير التلقائي
        await PricingService.delete_manual_cost(session, "whatsapp", "indonesia")
        assert await PricingService.get_manual_cost(session, "whatsapp", "indonesia") is None


@pytest.mark.asyncio
async def test_board_shows_manual_priced_country_without_live_provider():
    """الدولة المُسعّرة يدوياً تظهر باللوحة حتى لو المسار الحي ميت."""
    SettingsService._cache.clear()
    invalidate_board()
    await _seed_manual_pricing_db()

    async with async_session_maker() as session:
        await PricingService.set_manual_cost(
            session, "whatsapp", "indonesia", Decimal("0.5")
        )

    manager = _DeadLiveManager()

    async with async_session_maker() as session:
        result = await session.execute(
            select(NumberService).where(NumberService.code == "whatsapp")
        )
        service = result.scalar_one()
        entries = await build_board(session, service, manager=manager)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.code == "indonesia"
    assert entry.cost_usd == Decimal("0.5")
    # الهامش الافتراضي 50%: 0.5 → 0.75
    assert entry.sell_usd == Decimal("0.7500")


@pytest.mark.asyncio
async def test_manager_manual_overrides_live():
    """get_cheapest_price مع جلسة: التكلفة اليدوية تتجاوز المسار الحي."""
    from providers.manager import ProviderManager
    from types import SimpleNamespace

    SettingsService._cache.clear()
    await _seed_manual_pricing_db()

    async with async_session_maker() as session:
        await PricingService.set_manual_cost(
            session, "whatsapp", "indonesia", Decimal("0.42")
        )

    manager = ProviderManager()
    # نحقن مزوداً وهمياً بدل الاعتماد على مفاتيح الإعدادات
    manager._providers = {ProviderName.HEROSMS: object()}

    service = SimpleNamespace(
        code="whatsapp",
        herosms_code="wa",
        fivesim_code=None,
        sms_activate_code=None,
        smshub_code=None,
    )
    country = SimpleNamespace(code="indonesia", herosms_code="6",
                              fivesim_code=None, sms_activate_code=None, smshub_code=None)

    async with async_session_maker() as session:
        prices = await manager.get_cheapest_price(service, country, session=session)

    assert prices == {ProviderName.HEROSMS: Decimal("0.42")}


@pytest.mark.asyncio
async def test_reset_synced_countries_clears_manual_costs():
    """التصفير يمحو أيضاً الأسعار اليدوية للدول المحذوفة."""
    from handlers.admin.countries import _reset_synced_countries

    SettingsService._cache.clear()
    async with async_session_maker() as session:
        result = await session.execute(
            select(NumberService).where(NumberService.code == "whatsapp")
        )
        service = result.scalar_one()
        service.herosms_code = "wa"
        session.add(
            Country(
                code="indonesia",
                name_ar="إندونيسيا",
                flag="🇮🇩",
                herosms_code="6",
                is_active=True,
            )
        )
        await session.commit()
        await PricingService.set_manual_cost(
            session, "whatsapp", "indonesia", Decimal("0.5")
        )
        assert await PricingService.get_manual_cost(session, "whatsapp", "indonesia") is not None

        deleted = await _reset_synced_countries(session)
        assert deleted == 1
        assert await PricingService.get_manual_cost(session, "whatsapp", "indonesia") is None
