"""اختبارات إصلاح شراء أرقام HeroSMS ولوحة أسعار الدول.

يغطي:
1) HeroSMSProvider.get_price مع كل أشكال استجابة getPrices الحقيقية
   (بغلاف الدولة / بدونه / بمشغلين متداخلين) + فحص المخزون.
2) لوحة الأسعار: الترتيب من الأرخص للأغلى، استبعاد بلا سعر،
   تطبيق هامش الربح، الكاش.
3) كيبورد الدول بالأسعار.
4) تعريب الأسماء اللاتينية عند إعادة السحب وتجريد الحركات.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import CategoryType, Country, NumberService
from providers.herosms import HeroSMSProvider
from services import herosms_sync_service
from services.herosms_sync_service import _label_for
from services.number_catalog_service import (
    build_board,
    format_price,
    invalidate_board,
)


# ══════════════════════════════════════════════
# ══════════════ 1) get_price لأشكال HeroSMS ══════════════
# ══════════════════════════════════════════════


class FakeHeroSMSPrice(HeroSMSProvider):
    """مزود يرجع استجابة getPrices جاهزة."""

    def __init__(self, payload):
        super().__init__()
        self._payload = payload
        self.calls = 0

    async def _request(self, params: dict) -> str:
        self.calls += 1
        return json.dumps(self._payload)


@pytest.mark.asyncio
async def test_get_price_with_country_wrapper():
    """الشكل القياسي: {country: {service: {cost, count}}} — دولار مباشرة."""
    provider = FakeHeroSMSPrice({"6": {"wa": {"cost": 5.5, "count": 100}}})
    price = await provider.get_price("6", "wa")
    assert price == Decimal("5.5")


@pytest.mark.asyncio
async def test_get_price_without_country_wrapper():
    """شكل HeroSMS الفعلي: {service: {cost, count}} بدون غلاف الدولة.

    هذا هو الشكل الذي كان يُفشل كل عمليات الشراء قبل الإصلاح.
    """
    provider = FakeHeroSMSPrice({"wa": {"cost": 5.5, "count": 100}})
    price = await provider.get_price("6", "wa")
    assert price == Decimal("5.5")


@pytest.mark.asyncio
async def test_get_price_nested_operators_skips_zero_stock():
    """المشغلون المتداخلون: نتجاهل من مخزونه صفر ونأخذ الأرخص المتاح."""
    provider = FakeHeroSMSPrice(
        {
            "wa": {
                "best": {"cost": 4, "count": 0},
                "any": {"cost": 6, "count": 10},
            }
        }
    )
    price = await provider.get_price("6", "wa")
    assert price == Decimal("6")


@pytest.mark.asyncio
async def test_get_price_zero_stock_returns_none():
    provider = FakeHeroSMSPrice({"wa": {"cost": 5.5, "count": 0}})
    assert await provider.get_price("6", "wa") is None


@pytest.mark.asyncio
async def test_get_price_missing_count_allows():
    """غياب عداد المخزون لا يحجب السعر."""
    provider = FakeHeroSMSPrice({"wa": {"cost": 5.5}})
    assert await provider.get_price("6", "wa") == Decimal("5.5")


@pytest.mark.asyncio
async def test_get_price_service_missing_returns_none():
    provider = FakeHeroSMSPrice({"tg": {"cost": 7, "count": 5}})
    assert await provider.get_price("6", "wa") is None


@pytest.mark.asyncio
async def test_get_price_broken_json_returns_none():
    class Broken(FakeHeroSMSPrice):
        async def _request(self, params: dict) -> str:
            return "ERROR_SQL"

    provider = Broken({})
    assert await provider.get_price("6", "wa") is None


# ══════════════════════════════════════════════
# ══════════════ 2) لوحة الأسعار ══════════════
# ══════════════════════════════════════════════


class FakeManager:
    """مدير مزودين وهمي يرجع أسعاراً معدة سلفاً."""

    def __init__(self, prices: dict[str, dict]):
        self._prices = prices
        self.calls: list[str] = []

    async def get_cheapest_price(self, service, country, session=None):
        self.calls.append(country.code)
        return self._prices.get(country.code, {})


async def _seed_countries_for_board():
    async with async_session_maker() as session:
        result = await session.execute(
            select(NumberService).where(NumberService.code == "whatsapp")
        )
        service = result.scalar_one()
        service.herosms_code = "wa"
        session.add_all(
            [
                Country(code="indonesia", name_ar="إندونيسيا", flag="🇮🇩",
                        herosms_code="6", is_active=True),
                Country(code="vietnam", name_ar="فيتنام", flag="🇻🇳",
                        herosms_code="10", is_active=True),
                Country(code="india", name_ar="الهند", flag="🇮🇳",
                        herosms_code="22", is_active=True),
                Country(code="inactive_land", name_ar="دولة معطلة", flag="🏳",
                        herosms_code="77", is_active=False),
            ]
        )
        await session.commit()
        return service.code


@pytest.mark.asyncio
async def test_build_board_sorts_cheapest_first_and_applies_margin():
    invalidate_board()
    await _seed_countries_for_board()

    manager = FakeManager(
        {
            "indonesia": {"herosms": Decimal("0.05")},
            "vietnam": {"herosms": Decimal("0.02")},
            # الهند بلا أسعار → تُستبعد
        }
    )

    async with async_session_maker() as session:
        result = await session.execute(
            select(NumberService).where(NumberService.code == "whatsapp")
        )
        service = result.scalar_one()

        entries = await build_board(session, service, manager=manager)

    # دولة معطلة وبلا أسعار استُبعدتا، والترتيب من الأرخص
    assert [e.code for e in entries] == ["vietnam", "indonesia"]
    # هامش افتراضي 50%: 0.02 → 0.03
    assert entries[0].sell_usd == Decimal("0.0300")
    assert entries[0].name_ar == "فيتنام"
    assert entries[0].flag == "🇻🇳"


@pytest.mark.asyncio
async def test_build_board_uses_cache():
    invalidate_board()
    await _seed_countries_for_board()

    manager = FakeManager(
        {"indonesia": {"herosms": Decimal("0.05")}, "vietnam": {"herosms": Decimal("0.02")}}
    )

    async with async_session_maker() as session:
        result = await session.execute(
            select(NumberService).where(NumberService.code == "whatsapp")
        )
        service = result.scalar_one()

        await build_board(session, service, manager=manager)
        calls_after_first = len(manager.calls)
        await build_board(session, service, manager=manager)

    # الطلب الثاني من الكاش: لا طلبات جديدة للمزود
    assert len(manager.calls) == calls_after_first


def test_format_price_strips_unneeded_zeros():
    assert format_price(Decimal("0.0300")) == "0.03"
    assert format_price(Decimal("1.5000")) == "1.50"
    assert format_price(Decimal("0.0123")) == "0.013"


# ══════════════════════════════════════════════
# ══════════════ 3) الكيبورد بالأسعار ══════════════
# ══════════════════════════════════════════════


def test_countries_price_kb_shows_price_and_order():
    from keyboards.numbers import countries_price_kb
    from services.number_catalog_service import BoardEntry

    entries = [
        BoardEntry("vietnam", "فيتنام", "🇻🇳", Decimal("0.02"), Decimal("0.03")),
        BoardEntry("indonesia", "إندونيسيا", "🇮🇩", Decimal("0.05"), Decimal("0.075")),
    ]
    kb = countries_price_kb("whatsapp", entries)
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("فيتنام" in t and "0.03$" in t for t in texts), texts
    assert any("إندونيسيا" in t and "0.075$" in t for t in texts), texts
    # الأرخص أولاً
    assert texts[0].startswith("🇻🇳")


def test_countries_price_kb_pagination():
    from keyboards.numbers import countries_price_kb
    from services.number_catalog_service import BoardEntry

    entries = [
        BoardEntry(f"c{i}", f"دولة{i}", "🌍", Decimal("0.01"), Decimal("0.02"))
        for i in range(30)
    ]
    kb = countries_price_kb("whatsapp", entries, page=0)
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("التالي" in t for t in texts)

    kb_last = countries_price_kb("whatsapp", entries, page=2)
    texts_last = [btn.text for row in kb_last.inline_keyboard for btn in row]
    assert any("السابق" in t for t in texts_last)
    assert not any("التالي" in t for t in texts_last)


# ══════════════════════════════════════════════
# ══════════════ 4) تعريب الأسماء ══════════════
# ══════════════════════════════════════════════


def test_label_for_handles_accents():
    assert _label_for("Côte d'Ivoire")[0] == "ساحل العاج"
    assert _label_for("Türkiye")[0] == "تركيا"


def test_label_for_unknown_falls_back_safe():
    name, flag = _label_for("Atlantis Kingdom")
    assert name == "Atlantis Kingdom"
    assert flag == "🌍"


@pytest.mark.asyncio
async def test_resync_arabizes_latin_names():
    """إعادة السحب تترجم الأسماء اللاتينية القائمة إلى العربية."""
    async with async_session_maker() as session:

        class FakeProvider:
            async def get_countries(self):
                return [{"id": "6", "eng": "Indonesia"}]

            async def get_country_prices(self, country):
                return {"6": {"wa": {"cost": 5, "count": 10}}}

        # دولة قائمة باسم لاتيني من سحب سابق
        session.add(
            Country(
                code="indonesia",
                name_ar="Indonesia",
                flag="🇮🇩",
                herosms_code="6",
                is_active=True,
            )
        )
        await session.commit()

        await herosms_sync_service.sync_herosms_countries(
            session, ["whatsapp"], provider=FakeProvider()
        )

        result = await session.execute(
            select(Country).where(Country.herosms_code == "6")
        )
        country = result.scalar_one()
        assert country.name_ar == "إندونيسيا"


# ══════════════════════════════════════════════
# ══════════════ اكتشافات التوثيق الرسمي ══════════════
# ══════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_balance_is_usd_direct():
    """الرصيد بالدولار مباشرة — لا قسمة على 100 (خطأ العملة القديم)."""
    provider = FakeHeroSMSPrice({})
    provider._payload = None

    async def _balance_request(params):
        return "ACCESS_BALANCE:100.5"

    provider._request = _balance_request
    assert await provider.get_balance() == Decimal("100.5")


@pytest.mark.asyncio
async def test_get_prices_documented_shape():
    """الشكل الموثق حرفياً: {service: {cost, count, physicalCount}} بالدولار."""
    provider = FakeHeroSMSPrice(
        {"wa": {"cost": 0.08, "count": 16404053, "physicalCount": 654398}}
    )
    assert await provider.get_price("6", "wa") == Decimal("0.08")


@pytest.mark.asyncio
async def test_get_prices_list_shape():
    """شكل القائمة الموثق: [{service: {cost, count}}]."""
    provider = FakeHeroSMSPrice([{"wa": {"cost": 0.08, "count": 500}}])
    assert await provider.get_price("6", "wa") == Decimal("0.08")


@pytest.mark.asyncio
async def test_cancel_early_denied_returns_false():
    """رفض الإلغاء (أول دقيقتين) يرجع False لا True."""

    provider = FakeHeroSMSPrice({})

    async def _cancel_request(params):
        return "EARLY_CANCEL_DENIED"

    provider._request = _cancel_request
    assert await provider.cancel_order("123") is False


@pytest.mark.asyncio
async def test_cancel_confirmed_returns_true():
    provider = FakeHeroSMSPrice({})

    async def _cancel_request(params):
        return "ACCESS_CANCEL"

    provider._request = _cancel_request
    assert await provider.cancel_order("123") is True


@pytest.mark.asyncio
async def test_finish_confirmed_returns_true():
    provider = FakeHeroSMSPrice({})

    async def _finish_request(params):
        return "ACCESS_ACTIVATION"

    provider._request = _finish_request
    assert await provider.finish_order("123") is True


@pytest.mark.asyncio
async def test_buy_number_sends_max_price():
    """الشراء يمرر معامل maxPrice الرسمي عند توفره."""
    provider = FakeHeroSMSPrice({"wa": {"cost": 0.08, "count": 50}})
    captured: list[dict] = []

    async def _capturing_request(params):
        captured.append(dict(params))
        if params.get("action") == "getNumber":
            return "ACCESS_NUMBER:999:+6281234567"
        return json.dumps(provider._payload)

    provider._request = _capturing_request
    purchased = await provider.buy_number("6", "wa", max_price=Decimal("0.10"))

    assert purchased.phone_number
    buy_call = next(p for p in captured if p.get("action") == "getNumber")
    assert buy_call.get("maxPrice") == "0.10"


@pytest.mark.asyncio
async def test_get_countries_documented_list_shape():
    """getCountries ترجع قائمة حسب التوثيق الرسمي."""

    provider = FakeHeroSMSPrice({})

    async def _countries_request(params):
        return json.dumps(
            [
                {"id": 2, "rus": "Казахстан", "eng": "Kazakhstan",
                 "chn": "哈萨克斯坦", "visible": 1, "retry": 1},
                {"id": 99, "eng": "Hidden", "visible": 0, "retry": 1},
            ]
        )

    provider._request = _countries_request
    countries = await provider.get_countries()
    assert countries == [{"id": "2", "eng": "Kazakhstan"}]


@pytest.mark.asyncio
async def test_get_price_omits_service_param():
    """getPrice يجب ألا يمرر معامل service — نفس الطلب المجرّب في السحب.

    تمرير service كان يكسر الطلب عند بعض نسخ HeroSMS فتعود None بصمت
    ويظهر للمستخدم «لا توجد أرقام متاحة» رغم التوفر.
    """
    import json as _json

    from providers.herosms import HeroSMSProvider

    provider = HeroSMSProvider()
    captured: dict = {}
    response = _json.dumps({"6": {"wa": {"cost": 5.5, "count": 10}}})

    async def _request(params):
        captured.update(params)
        return response

    provider._request = _request
    assert await provider.get_price("6", "wa") == Decimal("5.5")
    assert captured.get("action") == "getPrices"
    assert captured.get("country") == "6"
    assert "service" not in captured, "يجب طلب كل خدمات الدولة ثم استخراج الخدمة"
