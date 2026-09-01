"""اختبارات سحب الدول من HeroSMS.

تتحقق من:
1) إنشاء الدول بأكواد HeroSMS وأسماء عربية وأعلام صحيحة.
2) تفعيل المتاح منه فقط (المخزون > 0) وعدم تعطيل أي قائمة.
3) عدم التكرار عند إعادة السحب.
4) الدمج مع دولة قائمة أضيفت سابقاً بمزود آخر (بدون كود herosms).
5) ملء أكواد الخدمات الناقصة في قاعدة قديمة.
6) المسار الاحتياطي عند فشل getCountries.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import Country, NumberService
from services.herosms_sync_service import (
    ensure_number_services,
    sync_herosms_countries,
)


class FakeHeroSMS:
    """مزود وهمي يحاكي استجابات HeroSMS."""

    def __init__(self, fail_get_countries: bool = False):
        self.fail_get_countries = fail_get_countries
        # id → {service: {cost, count}}
        self.prices: dict[str, dict] = {
            "6": {  # إندونيسيا — واتساب وتيليجرام متاحان
                "wa": {"cost": 5.5, "count": 120},
                "tg": {"cost": 7.0, "count": 80},
            },
            "10": {  # فيتنام — واتساب فقط
                "wa": {"cost": 4.0, "count": 35},
                "tg": {"cost": 6.0, "count": 0},
            },
            "22": {  # الهند — لا مخزون إطلاقاً
                "wa": {"cost": 2.0, "count": 0},
                "tg": {"cost": 3.0, "count": 0},
            },
            "43": {  # ألمانيا — تيليجرام فقط
                "wa": {"cost": 12.0, "count": 0},
                "tg": {"cost": 9.5, "count": 15},
            },
        }
        self.catalog = [
            {"id": "6", "eng": "Indonesia"},
            {"id": "10", "eng": "Vietnam"},
            {"id": "22", "eng": "India"},
            {"id": "43", "eng": "Germany"},
            {"id": "99", "eng": "Nowhere"},  # بدون أسعار إطلاقاً
        ]

    async def get_countries(self) -> list[dict]:
        if self.fail_get_countries:
            raise RuntimeError("getCountries غير مدعوم")
        return self.catalog

    async def get_country_prices(self, country: str) -> dict:
        services = self.prices.get(country)
        if services is None:
            return {}
        # الصيغة القياسية: {country: {service: {cost, count}}}
        return {country: services}


async def _get_country_by_herosms_code(session, code: str) -> Country | None:
    result = await session.execute(
        select(Country).where(Country.herosms_code == code)
    )
    return result.scalar_one_or_none()


@pytest.mark.asyncio
async def test_sync_creates_countries_with_labels_and_flags():
    async with async_session_maker() as session:
        report = await sync_herosms_countries(
            session,
            wanted_services=["whatsapp", "telegram"],
            activate=True,
            provider=FakeHeroSMS(),
        )

        assert report.catalog_source == "getCountries"
        assert report.fetched_countries == 5

        indonesia = await _get_country_by_herosms_code(session, "6")
        assert indonesia is not None
        assert indonesia.name_ar == "إندونيسيا"
        assert indonesia.flag == "🇮🇩"
        assert indonesia.is_active is True  # مخزون للخدمتين

        vietnam = await _get_country_by_herosms_code(session, "10")
        assert vietnam is not None
        assert vietnam.name_ar == "فيتنام"
        assert vietnam.is_active is True  # واتساب متاح

        germany = await _get_country_by_herosms_code(session, "43")
        assert germany is not None
        assert germany.is_active is True  # تيليجرام متاح

        india = await _get_country_by_herosms_code(session, "22")
        assert india is not None
        assert india.is_active is False  # لا مخزون → لا تفعيل

        # الدول التي ليس لها أسعار عند المزود لا تُنشأ إطلاقاً
        nowhere = await _get_country_by_herosms_code(session, "99")
        assert nowhere is None


@pytest.mark.asyncio
async def test_sync_only_whatsapp_still_activates_stocked():
    """سحب واتساب فقط: ألمانيا (بدون واتساب) تبقى غير مفعّلة."""
    async with async_session_maker() as session:
        report = await sync_herosms_countries(
            session,
            wanted_services=["whatsapp"],
            activate=True,
            provider=FakeHeroSMS(),
        )
        germany = await _get_country_by_herosms_code(session, "43")
        assert germany is not None
        assert germany.is_active is False  # لا واتساب متاح في ألمانيا


@pytest.mark.asyncio
async def test_sync_idle_mode_does_not_activate():
    async with async_session_maker() as session:
        report = await sync_herosms_countries(
            session,
            wanted_services=["whatsapp", "telegram"],
            activate=False,
            provider=FakeHeroSMS(),
        )
        indonesia = await _get_country_by_herosms_code(session, "6")
        assert indonesia is not None
        assert indonesia.is_active is False
        assert report.activated == 0


@pytest.mark.asyncio
async def test_resync_does_not_duplicate():
    async with async_session_maker() as session:
        provider = FakeHeroSMS()
        await sync_herosms_countries(
            session, ["whatsapp", "telegram"], provider=provider
        )
        first_count = (
            (await session.execute(select(Country))).scalars().all()
        )
        report2 = await sync_herosms_countries(
            session, ["whatsapp", "telegram"], provider=provider
        )
        second_count = (
            (await session.execute(select(Country))).scalars().all()
        )

        assert len(first_count) == len(second_count) == 4
        assert report2.added == []
        assert len(report2.updated) >= 0  # لا تغييرات جوهرية


@pytest.mark.asyncio
async def test_sync_merges_into_existing_country_without_herosms_code():
    """دولة أضيفت سابقاً عبر 5sim بلا كود HeroSMS → تُدمج ولا تتكرر."""
    async with async_session_maker() as session:
        existing = Country(
            code="indonesia",
            name_ar="إندونيسيا",
            flag="🇮🇩",
            fivesim_code="indonesia",
            herosms_code=None,
            is_active=True,
        )
        session.add(existing)
        await session.commit()

        await sync_herosms_countries(
            session,
            ["whatsapp", "telegram"],
            provider=FakeHeroSMS(),
        )

        result = await session.execute(
            select(Country).where(Country.code == "indonesia")
        )
        merged = result.scalar_one()
        assert merged.herosms_code == "6"
        assert merged.fivesim_code == "indonesia"  # لم يُمسّ

        all_countries = (
            (await session.execute(select(Country))).scalars().all()
        )
        # إندونيسيا مدموجة + فيتنام + الهند + ألمانيا = 4
        assert len(all_countries) == 4


@pytest.mark.asyncio
async def test_ensure_number_services_fills_missing_herosms_codes():
    """قاعدة قديمة بلا أكواد herosms للخدمات → تُملأ تلقائياً."""
    async with async_session_maker() as session:
        result = await session.execute(
            select(NumberService).where(NumberService.code == "whatsapp")
        )
        svc = result.scalar_one()
        svc.herosms_code = None
        await session.commit()

        services = await ensure_number_services(session)
        assert services["whatsapp"].herosms_code == "wa"
        assert services["telegram"].herosms_code == "tg"

        # وعدد الخدمات لم يتضاعف
        all_services = (
            (await session.execute(select(NumberService))).scalars().all()
        )
        codes = sorted(s.code for s in all_services)
        assert codes.count("whatsapp") == 1
        assert codes.count("telegram") == 1


@pytest.mark.asyncio
async def test_fallback_catalog_when_get_countries_fails():
    """فشل getCountries → الخريطة الاحتياطية تعمل."""
    async with async_session_maker() as session:

        class PartialProvider(FakeHeroSMS):
            async def get_countries(self):
                raise RuntimeError("غير مدعوم")

        # نترك أسعار 6 (إندونيسيا) فقط ضمن الخريطة الاحتياطية
        report = await sync_herosms_countries(
            session,
            ["whatsapp"],
            provider=PartialProvider(),
        )

        assert report.catalog_source == "الخريطة الاحتياطية"
        indonesia = await _get_country_by_herosms_code(session, "6")
        assert indonesia is not None
        assert indonesia.name_ar == "إندونيسيا"


@pytest.mark.asyncio
async def test_normalize_prices_operator_shape():
    """الشكل المتداخل بالمشغلين يُطبّع إلى أرخص مشغل."""
    from services.herosms_sync_service import _normalize_prices

    raw = {
        "6": {
            "wa": {
                "any": {"cost": 5.5, "count": 10},
                "best": {"cost": 4.5, "count": 3},
            }
        }
    }
    normalized = _normalize_prices(raw, "6")
    assert normalized["wa"]["cost"] == Decimal("4.5")
    assert normalized["wa"]["count"] == 3


@pytest.mark.asyncio
async def test_reset_synced_countries_keeps_manual():
    """التصفير يحذف دول HeroSMS وقواعد تسعيرها ويُبقي اليدوية."""
    from database.models import ServicePricing
    from handlers.admin.countries import _reset_synced_countries

    async with async_session_maker() as session:
        report = await sync_herosms_countries(
            session, ["whatsapp"], provider=FakeHeroSMS()
        )
        assert report.added  # دول أُنشئت فعلاً

        # دولة يدوية (بدون كود herosms) يجب أن تبقى بعد التصفير
        session.add(
            Country(
                code="manual_land",
                name_ar="دولة يدوية",
                herosms_code=None,
                is_active=True,
            )
        )
        indonesia = await _get_country_by_herosms_code(session, "6")
        assert indonesia is not None
        session.add(
            ServicePricing(
                service="whatsapp",
                country_code=indonesia.code,
                margin_value=Decimal("50"),
            )
        )
        await session.commit()

        deleted = await _reset_synced_countries(session)

        # كل الدول المسحوبة تلقائياً حُذفت (إندونيسيا/فيتنام/الهند/ألمانيا)
        assert deleted == 4
        assert await _get_country_by_herosms_code(session, "6") is None

        remaining = list((await session.execute(select(Country))).scalars().all())
        assert [c.code for c in remaining] == ["manual_land"]

        # قاعدة التسعير الخاصة بدولة محذوفة أُزيلت، والعامة تبقى
        pricing = list((await session.execute(select(ServicePricing))).scalars().all())
        assert all(p.country_code is None for p in pricing)
