"""Pricing per 1000, pulled catalog, join notify, and error-channel diagnosis."""

from __future__ import annotations

import inspect
from decimal import Decimal
from types import SimpleNamespace

import pytest

from database.engine import async_session_maker
from database.models import (
    ApiProvider,
    ApiProviderType,
    ApiProtocolType,
    Category,
    CategoryType,
    Product,
    ProductDisplayType,
    ProviderService,
    ProviderServiceStatus,
    SubCategory,
    User,
)
from middlewares.error_middleware import format_admin_error_report
from protocols.base import (
    ProtocolInsufficientFundsError,
    is_insufficient_funds_error,
)
from protocols.smm_v2 import SmmV2Protocol
from services.error_diagnosis_service import diagnose
from services.i18n_service import I18nService
from services.product_service import ProductService
from services.pulled_services_service import PulledServicesService
from services.smm_catalog import classify_smm_service


def test_not_enough_funds_is_detected_from_provider_code():
    assert is_insufficient_funds_error("neworder.error.not_enough_funds") is True
    assert is_insufficient_funds_error("ProtocolError: neworder.error.not_enough_funds") is True
    assert is_insufficient_funds_error("insufficient funds") is True
    assert is_insufficient_funds_error("invalid service") is False


def test_smm_v2_does_not_catch_clienttimeout_class():
    source = inspect.getsource(SmmV2Protocol._request)
    assert "except aiohttp.ClientTimeout" not in source
    assert "except ProtocolError" in source
    assert "is_insufficient_funds_error" in source


@pytest.mark.parametrize(
    "name, category, expected",
    [
        ("Instagram Likes [Real]", "Instagram", ("ig", "likes")),
        ("TikTok Followers", "TikTok Followers", ("tt", "followers")),
        ("YouTube Views", "YouTube", ("yt", "views")),
        ("Telegram Members", "Telegram", ("tg", "members")),
        ("Random Cheap Service", "Other", ("other", "other")),
    ],
)
def test_classify_smm_service_platform_and_kind(name, category, expected):
    assert classify_smm_service(name, category, None) == expected


def test_calculate_order_total_per_1000_not_min_quantity():
    product = SimpleNamespace(
        price_usd=Decimal("2"),
        requires_quantity=True,
        display_type=ProductDisplayType.PER_1000,
        min_quantity=100,
    )
    assert ProductService.calculate_order_total(product, 100) == Decimal("0.2000")
    assert ProductService.calculate_order_total(product, 1000) == Decimal("2.0000")


def test_calculate_order_total_skips_quantity_when_not_required():
    product = SimpleNamespace(
        price_usd=Decimal("5"),
        requires_quantity=False,
        display_type=ProductDisplayType.PER_1000,
        min_quantity=100,
    )
    assert ProductService.calculate_order_total(product, 500) == Decimal("5.0000")


def test_error_diagnosis_includes_solution_for_funds():
    exc = ProtocolInsufficientFundsError("neworder.error.not_enough_funds")
    diagnosis = diagnose(exc)
    assert "رصيد المزود" in diagnosis.title
    assert "اشحن" in diagnosis.solution
    report = format_admin_error_report(exc, event_name="CallbackQuery", tb="frame")
    assert "الحل المقترح" in report
    assert "not_enough_funds" in report


def test_error_diagnosis_for_clienttimeout_typeerror():
    exc = TypeError("catching classes that do not inherit from BaseException")
    diagnosis = diagnose(exc)
    assert diagnosis.severity == "critical"
    assert "except" in diagnosis.solution.lower() or "Exception" in diagnosis.solution


def test_locale_keys_for_funds_and_referral_join():
    I18nService._load.cache_clear()
    ar = I18nService.t("provider_insufficient_funds", "ar")
    en = I18nService.t("provider_insufficient_funds", "en")
    assert "رصيد المزود" in ar
    assert "provider" in en.lower()
    join = I18nService.t(
        "referral_join_notification",
        "ar",
        name="Ali",
        username="ali",
        user_id="1",
    )
    assert "Ali" in join
    assert "@ali" in join


@pytest.mark.asyncio
async def test_pulled_catalog_sorts_cheapest_first_and_does_not_publish():
    async with async_session_maker() as session:
        provider = ApiProvider(
            name="Panel",
            type=ApiProviderType.SMM,
            protocol_type=ApiProtocolType.SMM_V2,
            api_url="https://example.com/api/v2",
            api_key="secret",
            is_active=True,
        )
        session.add(provider)
        await session.flush()
        cheap = ProviderService(
            api_provider_id=provider.id,
            external_service_id="10",
            name="IG Likes cheap",
            category="Instagram Likes",
            rate=Decimal("0.20"),
            rate_usd=Decimal("0.20"),
            min_quantity=100,
            max_quantity=10000,
            status=ProviderServiceStatus.ACTIVE,
        )
        expensive = ProviderService(
            api_provider_id=provider.id,
            external_service_id="11",
            name="IG Likes premium",
            category="Instagram Likes",
            rate=Decimal("1.50"),
            rate_usd=Decimal("1.50"),
            min_quantity=50,
            max_quantity=5000,
            status=ProviderServiceStatus.ACTIVE,
        )
        other = ProviderService(
            api_provider_id=provider.id,
            external_service_id="12",
            name="TikTok Followers",
            category="TikTok",
            rate=Decimal("0.10"),
            rate_usd=Decimal("0.10"),
            min_quantity=100,
            max_quantity=10000,
            status=ProviderServiceStatus.ACTIVE,
        )
        session.add_all([cheap, expensive, other])
        await session.commit()

        platforms = await PulledServicesService.platform_counts(session)
        keys = {row[0] for row in platforms}
        assert "ig" in keys
        assert "tt" in keys

        services, total = await PulledServicesService.list_services(session, "ig", "likes")
        assert total == 2
        assert [s.external_service_id for s in services] == ["10", "11"]

        from sqlalchemy import func, select

        before = (await session.execute(select(func.count(Product.id)))).scalar_one()
        after = (await session.execute(select(func.count(Product.id)))).scalar_one()
        assert after == before


@pytest.mark.asyncio
async def test_publish_pulled_service_creates_storefront_product():
    async with async_session_maker() as session:
        category = Category(name_ar="رشق", emoji="📈", type=CategoryType.SMM, is_active=True)
        session.add(category)
        await session.flush()
        sub = SubCategory(
            category_id=category.id, name_ar="إنستغرام", emoji="📸", is_active=True
        )
        provider = ApiProvider(
            name="Panel",
            type=ApiProviderType.SMM,
            protocol_type=ApiProtocolType.SMM_V2,
            api_url="https://example.com/api/v2",
            api_key="secret",
            is_active=True,
        )
        session.add_all([sub, provider])
        await session.flush()
        service = ProviderService(
            api_provider_id=provider.id,
            external_service_id="99",
            name="IG Likes",
            category="Instagram Likes",
            rate=Decimal("0.40"),
            rate_usd=Decimal("0.40"),
            min_quantity=100,
            max_quantity=10000,
            requires_link=True,
            requires_quantity=True,
            status=ProviderServiceStatus.ACTIVE,
        )
        session.add(service)
        await session.commit()
        await session.refresh(service)
        await session.refresh(sub)

        product = await PulledServicesService.publish(
            session, service, sub.id, Decimal("1.2500")
        )
        assert product.id
        assert product.price_usd == Decimal("1.2500")
        assert product.cost_price_usd == Decimal("0.4000")
        assert product.provider_service_id == "99"
        assert product.display_type == ProductDisplayType.PER_1000
        assert product.requires_quantity is True
        assert ProductService.calculate_order_total(product, 100) == Decimal("0.1250")


@pytest.mark.asyncio
async def test_new_user_join_notifies_admin_and_referrer(monkeypatch):
    from middlewares.user_middleware import _notify_new_user_join

    sent_admin = []
    sent_user = []

    class FakeNotifier:
        def __init__(self, bot):
            self.bot = bot

        async def notify_admin_new_user(self, **kwargs):
            sent_admin.append(kwargs)

        async def notify_referrer_new_join(self, *args):
            sent_user.append(args)

    monkeypatch.setattr(
        "services.notification_service.NotificationService", FakeNotifier
    )

    async with async_session_maker() as session:
        referrer = User(
            telegram_id=1001, username="ref", full_name="Ref", language_code="ar"
        )
        session.add(referrer)
        await session.commit()
        await session.refresh(referrer)
        tg_user = SimpleNamespace(id=2002, username="new", full_name="New User")
        await _notify_new_user_join(object(), session, tg_user, referrer.id)

    assert sent_admin and sent_admin[0]["via_referral"] is True
    assert sent_admin[0]["telegram_id"] == 2002
    assert sent_user and sent_user[0][0] == 1001
    assert sent_user[0][2] == 2002


@pytest.mark.asyncio
async def test_new_user_notify_fail_open(monkeypatch):
    from middlewares.user_middleware import _notify_new_user_join

    class Boom:
        def __init__(self, bot):
            raise RuntimeError("no channel")

    monkeypatch.setattr("services.notification_service.NotificationService", Boom)
    tg_user = SimpleNamespace(id=9, username="x", full_name="x")
    await _notify_new_user_join(object(), None, tg_user, None)


def test_admin_main_keyboard_has_pulled_services_button():
    from keyboards.admin import admin_main_kb

    kb = admin_main_kb()
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "📥 خدمات مسحوبة" in labels
    assert "admin:pulled_services" in datas


def test_error_middleware_report_includes_solution_not_only_traceback():
    from middlewares.error_middleware import format_admin_error_report

    class Dummy(Exception):
        pass

    text = format_admin_error_report(
        Dummy("boom"),
        event_name="Message",
        user_id=1,
        username="u",
        callback_data="prod_confirm:1",
        text="/start",
        tb="Traceback (most recent call last):\n  File 'x.py', line 1",
    )
    assert "الحل المقترح" in text
    assert "prod_confirm:1" in text
    assert "boom" in text
