"""
اختبارات الإضافات الثلاث الهدية: مركز القيادة، محرك الاسترجاع الموحّد،
والحارس الذاتي.

تركّز على ما يهم فعلاً:
- الاسترجاع لا يُدفع مرتين ولا يُدفع لمن لا يستحق.
- الحارس يعطّل الميزات الخطرة فعلاً ويعيدها فعلاً.
- مركز القيادة يجمع أرقاماً صحيحة لا صفراً.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from database.engine import async_session_maker
from database.models import (
    NumberOrder,
    OrderStatus,
    ProviderName,
    Transaction,
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from services.bot_command_service import (
    CockpitService,
    RefundError,
    SentinelService,
    UnifiedRefundService,
)
from services.feature_service import FeatureService


async def _enable(*keys: str) -> None:
    async with async_session_maker() as session:
        for key in keys:
            await FeatureService.set_enabled(session, key, True)


async def _disable(*keys: str) -> None:
    async with async_session_maker() as session:
        for key in keys:
            await FeatureService.set_enabled(session, key, False)


async def _user(user_id: int, balance: str = "0") -> None:
    async with async_session_maker() as session:
        if await session.get(User, user_id) is not None:
            return
        session.add(
            User(
                id=user_id,
                telegram_id=user_id * 10,
                username=f"user{user_id}",
                full_name=f"U{user_id}",
                balance=Decimal(balance),
                joined_at=datetime.utcnow() - timedelta(days=5),
            )
        )
        await session.commit()


async def _product() -> None:
    """منتج حقيقي لأن unified_orders.product_id مفتاح أجنبي."""
    from database.models import Category, CategoryType, Product, ProductDisplayType
    from database.models import ProductPricingType, ProductStatus, SubCategory
    from sqlalchemy import select

    async with async_session_maker() as session:
        if await session.get(Product, 1) is not None:
            return
        category = (await session.execute(select(Category).limit(1))).scalars().first()
        if category is None:
            category = Category(name_ar="رشق", type=CategoryType.SMM, is_active=True)
            session.add(category)
            await session.flush()
        sub = (await session.execute(select(SubCategory).limit(1))).scalars().first()
        if sub is None:
            sub = SubCategory(category_id=category.id, name_ar="تيك توك", is_active=True)
            session.add(sub)
            await session.flush()
        session.add(
            Product(
                id=1, sub_category_id=sub.id, name_ar="1000 متابع",
                price_usd=Decimal("10.00"), cost_price_usd=Decimal("5"),
                pricing_type=ProductPricingType.FIXED,
                display_type=ProductDisplayType.PER_1000,
                status=ProductStatus.ACTIVE, min_quantity=1, max_quantity=100000,
            )
        )
        await session.commit()


async def _balance(user_id: int) -> Decimal:
    async with async_session_maker() as session:
        return Decimal(str((await session.get(User, user_id)).balance))


# ═══════════════════════════ مركز القيادة ═══════════════════════════


@pytest.mark.asyncio
async def test_cockpit_reports_real_numbers_not_zeroes():
    await _enable("bot_cockpit")
    await _user(5101, "25")

    async with async_session_maker() as session:
        session.add(
            NumberOrder(
                id=5101, user_id=5101, provider=ProviderName.FIVESIM,
                provider_order_id="ck1", service="tg", country_code="tr",
                phone_number="+905555101000",
                price_provider_usd=Decimal("0.2"), price_sell_usd=Decimal("0.5"),
                status=OrderStatus.PENDING,
                expires_at=datetime.utcnow() + timedelta(minutes=5),
            )
        )
        await session.commit()

        data = await CockpitService.snapshot(session)

    assert data["users"] >= 1
    assert data["held_balance_usd"] >= Decimal("25")
    assert data["pending_numbers"] >= 1
    assert data["features_total"] >= 60
    assert data["features_enabled"] >= 1
    assert isinstance(data["alerts"], list)


@pytest.mark.asyncio
async def test_cockpit_renders_readable_text():
    await _enable("bot_cockpit")
    async with async_session_maker() as session:
        data = await CockpitService.snapshot(session)
    text = CockpitService.render(data)
    assert "مركز قيادة البوت" in text
    assert "المستخدمون" in text
    assert "الأموال" in text
    assert "الإضافات" in text


# ═══════════════════════════ محرك الاسترجاع ═══════════════════════════


@pytest.mark.asyncio
async def test_refund_number_order_pays_once_and_records_reason():
    await _enable("unified_refund")
    await _user(5201, "0")

    async with async_session_maker() as session:
        session.add(
            NumberOrder(
                id=5201, user_id=5201, provider=ProviderName.FIVESIM,
                provider_order_id="rf1", service="tg", country_code="tr",
                phone_number="+905555201000",
                price_provider_usd=Decimal("0.2"), price_sell_usd=Decimal("0.50"),
                status=OrderStatus.PENDING,
            )
        )
        await session.commit()

        before = (await session.get(User, 5201)).balance
        result = await UnifiedRefundService.refund_number_order(
            session, 5201, user_id=5201, reason="no_code", admin_id=1
        )
        after = (await session.get(User, 5201)).balance

    assert result["amount_usd"] == Decimal("0.50")
    assert after == before + Decimal("0.50")

    # لا يُدفع مرتين
    async with async_session_maker() as session:
        with pytest.raises(RefundError):
            await UnifiedRefundService.refund_number_order(
                session, 5201, user_id=5201, reason="no_code", admin_id=1
            )
    assert await _balance(5201) == after


@pytest.mark.asyncio
async def test_refund_rejects_order_that_does_not_exist():
    await _enable("unified_refund")
    async with async_session_maker() as session:
        with pytest.raises(RefundError):
            await UnifiedRefundService.refund_number_order(
                session, 999999, user_id=5202, reason="no_code", admin_id=1
            )


@pytest.mark.asyncio
async def test_partial_order_refunds_only_the_undelivered_share():
    """في الطلب الجزئي يُسترجع نصيب ما لم يُنفَّذ فقط، لا كامل السعر."""
    await _enable("unified_refund")
    await _user(5301, "0")
    await _product()

    async with async_session_maker() as session:
        session.add(
            UnifiedOrder(
                id=5301, user_id=5301, product_id=1, quantity=1000,
                target="https://x", price_usd=Decimal("10.00"),
                cost_price_usd=Decimal("5"),
                status=UnifiedOrderStatus.PARTIAL, remains=250,
            )
        )
        await session.commit()

        before = (await session.get(User, 5301)).balance
        result = await UnifiedRefundService.refund_unified_order(
            session, 5301, user_id=5301, reason="provider_failure", admin_id=1
        )
        after = (await session.get(User, 5301)).balance

    # 250 من 1000 لم تُنفَّذ = 25% من 10$ = 2.5$
    assert result["amount_usd"] == Decimal("2.5000")
    assert after == before + Decimal("2.5000")


@pytest.mark.asyncio
async def test_refund_report_breaks_down_by_reason():
    """قبل التوحيد لم يكن ممكناً الإجابة عن «كم استرجعنا ولماذا»."""
    await _enable("unified_refund")
    await _user(5401, "0")

    async with async_session_maker() as session:
        for index in range(2):
            session.add(
                NumberOrder(
                    id=5400 + index, user_id=5401, provider=ProviderName.FIVESIM,
                    provider_order_id=f"rp{index}", service="tg", country_code="tr",
                    phone_number=f"+90555540{index}000",
                    price_provider_usd=Decimal("0.2"), price_sell_usd=Decimal("1.00"),
                    status=OrderStatus.PENDING,
                )
            )
        await session.commit()
        await UnifiedRefundService.refund_number_order(
            session, 5400, user_id=5401, reason="no_code", admin_id=1
        )
        await UnifiedRefundService.refund_number_order(
            session, 5401, user_id=5401, reason="provider_failure", admin_id=1
        )

        report = await UnifiedRefundService.report(session, 30)

    assert report["count"] >= 2
    assert report["total_usd"] >= Decimal("2.00")
    reasons = {row["reason"] for row in report["by_reason"]}
    assert "لم يصل الكود" in reasons
    assert "فشل المزود" in reasons


# ═══════════════════════════ الحارس الذاتي ═══════════════════════════


@pytest.mark.asyncio
async def test_sentinel_disables_risky_features_and_restores_them():
    await _enable("self_heal_sentinel", "peer_marketplace", "bulk_numbers")

    async with async_session_maker() as session:
        disabled = await SentinelService.engage_safe_mode(session, "اختبار")

    assert "peer_marketplace" in disabled
    assert "bulk_numbers" in disabled
    assert await FeatureService.enabled("peer_marketplace") is False
    assert await FeatureService.enabled("bulk_numbers") is False
    assert await SentinelService.is_safe_mode() is True

    async with async_session_maker() as session:
        restored = await SentinelService.disengage_safe_mode(session)

    assert "peer_marketplace" in restored
    assert await FeatureService.enabled("peer_marketplace") is True
    assert await SentinelService.is_safe_mode() is False


@pytest.mark.asyncio
async def test_sentinel_engages_automatically_over_threshold():
    await _enable("self_heal_sentinel", "bulk_numbers")

    async with async_session_maker() as session:
        await FeatureService.set_option(session, "self_heal_sentinel", "error_threshold", 3)
        await FeatureService.set_option(session, "self_heal_sentinel", "window_minutes", 10)

        for _ in range(5):
            await SentinelService.record_error(session, "provider")

        result = await SentinelService.check(session)

    assert result["action"] == "engaged"
    assert "bulk_numbers" in result["disabled"]
    assert await SentinelService.is_safe_mode() is True


@pytest.mark.asyncio
async def test_sentinel_holds_below_threshold():
    await _enable("self_heal_sentinel")
    async with async_session_maker() as session:
        await FeatureService.set_option(session, "self_heal_sentinel", "error_threshold", 50)
        # وضع نظيف: لا أخطاء مسجلة في هذه النافذة
        result = await SentinelService.check(session)
    assert result["action"] in ("holding", "disengaged")


@pytest.mark.asyncio
async def test_sentinel_status_exposes_what_is_disabled():
    await _enable("self_heal_sentinel")
    async with async_session_maker() as session:
        await SentinelService.engage_safe_mode(session, "اختبار الحالة")
        status = await SentinelService.status(session)

    assert status["safe_mode"] is True
    assert status["reason"] == "اختبار الحالة"
    assert set(status["currently_disabled"]) <= set(SentinelService.RISKY_FEATURES)

    async with async_session_maker() as session:
        await SentinelService.disengage_safe_mode(session)


@pytest.mark.asyncio
async def test_sentinel_disabled_does_nothing():
    await _disable("self_heal_sentinel")
    async with async_session_maker() as session:
        assert await SentinelService.check(session) == {"action": "disabled"}
