"""اختبارات بناء الشاشة الأولى وأقسام الأرقام والمتجر وسوق المستخدمين.

الهدف: تثبيت التصميم المطلوب حتى لا يتراجع مع أي تعديل لاحق:
- ثلاثة أزرار عريضة (مستطيلة) فوق بعضها: الأرقام ← المتجر ← سوق المستخدمين.
- بقية الأزرار مربّعات: كل زرين جنب بعض.
- قسم الأرقام يضم خدمات الأرقام (واتساب/تليجرام) + أرقام تليجرام الجاهزة.
- المتجر يضم الأقسام الخمسة وكل ما يضيفه الأدمن.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    NumberService,
    Product,
    ProductFulfillmentType,
    ProductPricingType,
    ProductStatus,
    SubCategory,
)
from services.feature_service import FeatureService
from services.marketplace_service import MarketplaceService


def _rows(markup):
    return [row for row in markup.inline_keyboard]


def _callbacks(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


# ══════════════ القائمة الرئيسية ══════════════


def test_main_menu_has_three_wide_sections_then_square_pairs():
    from keyboards.main_menu import build_main_menu

    markup = build_main_menu(
        number_services=[],
        categories=[],
        balance_usd="5.00",
        language="ar",
        show_marketplace=True,
        completed_orders_count=7,
    )
    rows = _rows(markup)

    # الأزرار الثلاثة العريضة: كل واحد صف كامل.
    assert [len(row) for row in rows[:3]] == [1, 1, 1]
    assert [button.callback_data for row in rows[:3] for button in row] == [
        "num_hub",
        "store:home",
        "market:home",
    ]

    # بقية الأزرار مربّعات: كل صف فيه زران.
    for row in rows[3:]:
        assert len(row) == 2, [button.callback_data for button in row]

    callbacks = _callbacks(markup)
    assert "menu:deposit" in callbacks
    assert "menu:support" in callbacks
    assert "menu:account" in callbacks
    assert "info:stats" in callbacks
    assert "info:terms" in callbacks


def test_main_menu_marketplace_button_is_controlled_by_feature():
    from keyboards.main_menu import build_main_menu

    assert "market:home" in _callbacks(build_main_menu([], [], show_marketplace=True))
    assert "market:home" not in _callbacks(build_main_menu([], [], show_marketplace=False))


@pytest.mark.asyncio
async def test_build_menu_shows_marketplace_only_when_feature_enabled():
    from handlers.start import _build_menu

    class _FakeUser:
        balance = Decimal("1.00")
        language_code = "ar"

    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "peer_marketplace", False)
        hidden = _callbacks(await _build_menu(session, _FakeUser()))
        assert "market:home" not in hidden

        await FeatureService.set_enabled(session, "peer_marketplace", True)
        shown = _callbacks(await _build_menu(session, _FakeUser()))
        assert shown[:3] == ["num_hub", "store:home", "market:home"]


# ══════════════ قسم الأرقام ══════════════


def test_numbers_hub_lists_services_ready_telegram_and_packages():
    from keyboards.numbers import numbers_hub_kb

    services = [
        NumberService(code="whatsapp", name_ar="واتساب", emoji="💬"),
        NumberService(code="telegram", name_ar="تيليجرام", emoji="✈️"),
    ]
    markup = numbers_hub_kb(services, back_to_store=False, tg_ready=True, packages=True)
    callbacks = _callbacks(markup)

    assert callbacks == [
        "num_svc:whatsapp",
        "num_svc:telegram",
        "num_packages",
        "tgready:list",
        "back_to_main",
    ]
    assert any("واتساب" in button.text for row in markup.inline_keyboard for button in row)
    assert any("تيليجرام" in button.text for row in markup.inline_keyboard for button in row)


def test_numbers_hub_hides_empty_sections():
    from keyboards.numbers import numbers_hub_kb

    services = [NumberService(code="whatsapp", name_ar="واتساب", emoji="💬")]
    callbacks = _callbacks(numbers_hub_kb(services, back_to_store=True))
    assert callbacks == ["num_svc:whatsapp", "store:home"]
    assert "tgready:list" not in callbacks
    assert "num_packages" not in callbacks


@pytest.mark.asyncio
async def test_numbers_hub_handler_opens_ready_telegram_and_returns_to_origin():
    from handlers.numbers import numbers_hub

    class _Message:
        def __init__(self):
            self.text = None
            self.reply_markup = None

        async def edit_text(self, text, reply_markup=None):
            self.text = text
            self.reply_markup = reply_markup

    class _Callback:
        def __init__(self, data):
            self.data = data
            self.message = _Message()

        async def answer(self):
            return None

    class _FakeService:
        code = "telegram"
        name_ar = "تيليجرام"
        emoji = "✈️"

    async with async_session_maker() as session:
        from unittest.mock import patch

        with patch("handlers.numbers.get_active_number_services", return_value=[_FakeService()]):
            callback = _Callback("num_hub")
            await numbers_hub(callback, session)
            assert _callbacks(callback.message.reply_markup)[-1] == "back_to_main"

            from_store = _Callback("num_hub:store")
            await numbers_hub(from_store, session)
            assert _callbacks(from_store.message.reply_markup)[-1] == "store:home"
            assert "الأرقام" in from_store.message.text


# ══════════════ المتجر وأقسامه الخمسة ══════════════


def test_store_section_service_exposes_the_five_primary_sections():
    from services.store_section_service import BUILTIN_ENTRIES, PRIMARY_SECTIONS

    labels = {key: label for key, label, _action, _order in BUILTIN_ENTRIES}
    assert labels["smart_smm"] == "🚀 الرشق"
    assert labels["smart_games"] == "🎮 شحن الألعاب"
    assert labels["smart_apps"] == "📱 شحن البرامج"
    assert labels["smart_balances"] == "💳 شحن الرصيد"
    assert labels["smart_subscriptions"] == "✨ الاشتراكات الرقمية"

    # الأقسام الخمسة تأتي قبل بقية الأقسام الذكية.
    orders = {key: order for key, _label, _action, order in BUILTIN_ENTRIES}
    for key, _type, _label in PRIMARY_SECTIONS:
        assert orders[key] < orders["smart_featured"]


async def _seed_categories(session):
    smm = Category(name_ar="قسم الرشق", emoji="🚀", type=CategoryType.SMM, sort_order=10, is_active=True)
    games = Category(name_ar="قسم شحن الألعاب", emoji="🎮", type=CategoryType.GAMES, sort_order=20, is_active=True)
    session.add_all([smm, games])
    await session.commit()
    await session.refresh(smm)
    await session.refresh(games)
    return smm, games


@pytest.mark.asyncio
async def test_store_page_resolves_primary_sections_to_categories_without_duplicates():
    from services.store_section_service import StoreSectionService

    async with async_session_maker() as session:
        smm, games = await _seed_categories(session)
        entries = await StoreSectionService.list_entries(include_inactive=True)

    page = StoreSectionService.build_page_entries(entries, [smm, games])
    actions = [entry.action for entry in page]

    # الرشق والألعاب يفتحان الفئة الحقيقية (تحفظ تنقّل الأقسام الداخلية).
    assert f"cat:{smm.id}" in actions
    assert f"cat:{games.id}" in actions
    # لا تتكرر الفئة نفسها كزر فئة عادي.
    assert actions.count(f"cat:{smm.id}") == 1
    assert actions.count(f"cat:{games.id}") == 1
    # بقية الأقسام التي بلا فئة تبقى أقساماً ذكية.
    assert "store:section:balances" in actions
    assert "store:section:subscriptions" in actions
    # أدوات المتجر وأزرار الأدمن محفوظة.
    assert "menu:cart" in actions
    assert "menu:search" in actions


@pytest.mark.asyncio
async def test_store_page_keeps_admin_custom_sections_and_can_hide_a_primary_one():
    from services.store_section_service import StoreSectionService

    async with async_session_maker() as session:
        custom = await StoreSectionService.add_custom(session, "🎯 قسم مخصص", "cat:999")
        await StoreSectionService.set_active(session, "smart_games", False)
        entries = await StoreSectionService.list_entries(include_inactive=True)

    page = StoreSectionService.build_page_entries(entries, [])
    actions = [entry.action for entry in page]
    labels = [entry.label for entry in page]

    assert custom.action in actions
    assert "🎯 قسم مخصص" in labels
    assert "store:section:games" not in actions


@pytest.mark.asyncio
async def test_store_home_screen_renders_the_five_sections():
    from keyboards.store import store_home_kb
    from services.store_section_service import StoreSectionService

    async with async_session_maker() as session:
        smm, games = await _seed_categories(session)
        entries = await StoreSectionService.list_entries(include_inactive=True)

    page = StoreSectionService.build_page_entries(entries, [smm, games])
    markup = store_home_kb(entries=page, language="ar")
    callbacks = _callbacks(markup)
    texts = [button.text for row in markup.inline_keyboard for button in row]

    assert f"cat:{smm.id}" in callbacks
    assert "🚀 الرشق" in texts
    assert "🎮 شحن الألعاب" in texts
    assert "📱 شحن البرامج" in texts
    assert "💳 شحن الرصيد" in texts
    assert "✨ الاشتراكات الرقمية" in texts
    # زر الأرقام داخل المتجر يعود إلى المتجر نفسه.
    assert "num_hub:store" in callbacks


@pytest.mark.asyncio
async def test_store_discovery_lists_balance_and_subscription_products():
    from services.store_discovery_service import StoreDiscoveryService

    async with async_session_maker() as session:
        balance_cat = Category(
            name_ar="قسم الأرصدة", emoji="💳", type=CategoryType.BALANCES, sort_order=1, is_active=True
        )
        sub_cat = Category(
            name_ar="قسم الاشتراكات", emoji="✨", type=CategoryType.SUBSCRIPTIONS, sort_order=2, is_active=True
        )
        session.add_all([balance_cat, sub_cat])
        await session.commit()
        await session.refresh(balance_cat)
        await session.refresh(sub_cat)

        balance_sub = SubCategory(category_id=balance_cat.id, name_ar="أرصدة ألعاب", emoji="🎮", is_active=True)
        sub_sub = SubCategory(category_id=sub_cat.id, name_ar="شات جي بي تي", emoji="🤖", is_active=True)
        session.add_all([balance_sub, sub_sub])
        await session.commit()

        session.add_all(
            [
                Product(
                    sub_category_id=balance_sub.id,
                    name_ar="رصيد ببجي 60 UC",
                    price_usd=Decimal("1.50"),
                    pricing_type=ProductPricingType.FIXED,
                    fulfillment_type=ProductFulfillmentType.INVENTORY,
                    status=ProductStatus.ACTIVE,
                ),
                Product(
                    sub_category_id=sub_sub.id,
                    name_ar="اشتراك ChatGPT Plus",
                    price_usd=Decimal("6.00"),
                    pricing_type=ProductPricingType.FIXED,
                    fulfillment_type=ProductFulfillmentType.INVENTORY,
                    status=ProductStatus.ACTIVE,
                ),
            ]
        )
        await session.commit()

        balances = await StoreDiscoveryService.products(session, "balances")
        subscriptions = await StoreDiscoveryService.products(session, "subscriptions")

    assert [product.name_ar for product in balances] == ["رصيد ببجي 60 UC"]
    assert [product.name_ar for product in subscriptions] == ["اشتراك ChatGPT Plus"]


# ══════════════ سوق المستخدمين ══════════════


@pytest.mark.asyncio
async def test_marketplace_is_reachable_from_main_menu_and_creates_merchant_profile():
    from datetime import datetime

    from database.models import User
    from services.market_profile_service import MarketProfileService

    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "peer_marketplace", True)
        assert await MarketplaceService.enabled() is True

        user = User(telegram_id=770001, username="tager", balance=Decimal("5"), joined_at=datetime.utcnow())
        session.add(user)
        await session.commit()
        await session.refresh(user)

        created = await MarketProfileService.create(session, user.id, "tager_test", "secret123")

    assert created.alias == "tager_test"
    assert MarketProfileService.verify_password("secret123", created.password_hash) is True
    assert MarketProfileService.verify_password("wrong-pass", created.password_hash) is False


@pytest.mark.asyncio
async def test_marketplace_short_alias_is_rejected():
    from services.market_profile_service import MarketProfileError, MarketProfileService

    async with async_session_maker() as session:
        with pytest.raises(MarketProfileError):
            await MarketProfileService.create(session, None, "ab", "secret123")
        with pytest.raises(MarketProfileError):
            await MarketProfileService.create(session, None, "name with spaces", "secret123")
