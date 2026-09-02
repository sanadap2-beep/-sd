"""
اختبارات الموجة الثانية: Failover الكتالوج، مراقبة الطلبات المتوازية،
كاش الأسعار، وإدارة دورة حياة الاشتراكات.

تركّز على ما يمكن أن ينكسر بصمت: تطابق أنواع الكاش، ترتيب المسارات،
وأن إيقاف الميزة يعيد السلوك القديم حرفياً.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    ApiProvider,
    ApiProviderType,
    ApiProtocolType,
    Base,
    Category,
    CategoryType,
    NumberOrder,
    OrderStatus,
    Product,
    ProductDisplayType,
    ProductFulfillmentType,
    ProductPricingType,
    ProductStatus,
    ProviderName,
    SubCategory,
    SubscriptionPlan,
    User,
    UserSubscription,
)
from services.catalog_routing_service import CatalogRoutingService
from services.feature_service import FeatureService
from services.price_cache_service import PriceCacheService
from services.subscription_lifecycle_service import (
    SubscriptionError,
    SubscriptionLifecycleService,
)


async def _seed_catalog() -> None:
    """يبني قسماً ومنتجاً ومزودين ومستخدماً — بشكل idempotent بالكامل."""
    async with async_session_maker() as session:
        # مستخدم مخصص للاشتراكات. لا نستخدم id=1 لأن init_db() يبذر
        # أدمن عند هذا المعرف برصيد صفر، فيفشل التجديد لسبب لا علاقة له
        # بالمنطق المختبَر.
        subscriber = await session.get(User, 501)
        if subscriber is None:
            session.add(
                User(
                    id=501, telegram_id=900501, username="sub1", full_name="S",
                    balance=Decimal("100"),
                    joined_at=datetime.utcnow() - timedelta(hours=9),
                )
            )
        elif subscriber.balance < Decimal("100"):
            subscriber.balance = Decimal("100")
        await session.commit()

        if await session.get(Product, 1) is not None:
            return

        category = Category(name_ar="رشق", type=CategoryType.SMM, is_active=True)
        session.add(category)
        await session.flush()
        sub = SubCategory(category_id=category.id, name_ar="تيك توك", is_active=True)
        session.add(sub)
        await session.flush()
        if await session.get(ApiProvider, 1) is None:
            session.add(
                ApiProvider(
                    id=1, name="Primary", type=ApiProviderType.SMM,
                    protocol_type=ApiProtocolType.SMM_V2, api_url="http://a",
                    api_key="k", is_active=True, priority=1,
                )
            )
        if await session.get(ApiProvider, 2) is None:
            session.add(
                ApiProvider(
                    id=2, name="Backup", type=ApiProviderType.SMM,
                    protocol_type=ApiProtocolType.SMM_V2, api_url="http://b",
                    api_key="k", is_active=True, priority=2,
                )
            )
        await session.flush()
        session.add(
            Product(
                id=1, sub_category_id=sub.id, api_provider_id=1,
                provider_service_id="svc-A", name_ar="1000 متابع",
                price_usd=Decimal("2"), cost_price_usd=Decimal("1"),
                pricing_type=ProductPricingType.FIXED,
                display_type=ProductDisplayType.PER_1000,
                fulfillment_type=ProductFulfillmentType.API,
                status=ProductStatus.ACTIVE, requires_link=True,
                min_quantity=1, max_quantity=100000,
            )
        )
        await session.commit()


async def _ensure_plan(price: str = "9", days: int = 30) -> None:
    """ينشئ خطة اشتراك مرجعية إن لم تكن موجودة."""
    async with async_session_maker() as session:
        if await session.get(SubscriptionPlan, 1) is None:
            session.add(
                SubscriptionPlan(
                    id=1, product_id=1, days=days, price_usd=Decimal(price),
                    auto_renew_default=True, is_active=True,
                )
            )
            await session.commit()


# ═══════════════════════════ كاش الأسعار ═══════════════════════════


@pytest.mark.asyncio
async def test_price_cache_preserves_enum_keys_and_decimal_values():
    """
    regression: str(ProviderName.FIVESIM) يعطي "ProviderName.FIVESIM" بينما
    القيمة الحقيقية "fivesim". لو سُلسل المفتاح بـ str() لضاع التطابق
    وعاد الكاش بلا فائدة، ولانكسر فهرس self._providers في provider_manager.
    """
    original = {
        ProviderName.FIVESIM: Decimal("0.1234"),
        ProviderName.SMSHUB: Decimal("0.0900"),
    }
    await PriceCacheService.set("test:price:tg:tr", original, ttl=60)
    restored = await PriceCacheService.get("test:price:tg:tr")

    assert restored == original
    assert isinstance(restored[ProviderName.FIVESIM], Decimal)
    assert isinstance(next(iter(restored)), ProviderName)

    # نفس العملية التي يقوم بها provider_manager
    assert min(restored, key=restored.get) == ProviderName.SMSHUB

    assert await PriceCacheService.invalidate("test:price:tg:tr") >= 1
    assert await PriceCacheService.get("test:price:tg:tr") is None


@pytest.mark.asyncio
async def test_price_cache_ttl_is_admin_tunable():
    assert await PriceCacheService.ttl_seconds() == 20
    async with async_session_maker() as session:
        await FeatureService.set_option(session, "redis_cache", "price_ttl_seconds", 45)
    assert await PriceCacheService.ttl_seconds() == 45
    async with async_session_maker() as session:
        await FeatureService.set_option(session, "redis_cache", "price_ttl_seconds", 20)


# ═══════════════════════════ Failover الكتالوج ═══════════════════════════


@pytest.mark.asyncio
async def test_failover_disabled_keeps_single_route_legacy_behaviour():
    await _seed_catalog()
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "catalog_failover", False)
        await CatalogRoutingService.add_route(session, 1, 2, "svc-B", priority=100)
        product = await session.get(Product, 1)
        routes = await CatalogRoutingService.routes_for(session, product)
    assert len(routes) == 1
    assert routes[0].api_provider_id == 1
    assert routes[0].is_primary is True


@pytest.mark.asyncio
async def test_failover_orders_primary_before_backup():
    await _seed_catalog()
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "catalog_failover", True)
        await CatalogRoutingService.add_route(session, 1, 2, "svc-B", priority=100)
        product = await session.get(Product, 1)
        routes = await CatalogRoutingService.routes_for(session, product)
    assert [r.api_provider_id for r in routes] == [1, 2]
    assert routes[1].provider_service_id == "svc-B"


@pytest.mark.asyncio
async def test_product_stays_available_when_primary_provider_dies():
    """هذا هو جوهر الإصلاح: موت المزود لم يعد يُغلق المنتج."""
    await _seed_catalog()
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "catalog_failover", True)
        await CatalogRoutingService.add_route(session, 1, 2, "svc-B", priority=100)
        provider = await session.get(ApiProvider, 1)
        provider.is_active = False
        await session.commit()

        product = await session.get(Product, 1)
        routes = await CatalogRoutingService.routes_for(session, product)

    assert len(routes) == 1
    assert routes[0].api_provider_id == 2

    async with async_session_maker() as session:
        provider = await session.get(ApiProvider, 1)
        provider.is_active = True
        await session.commit()


@pytest.mark.asyncio
async def test_add_route_is_idempotent_and_coverage_report_counts():
    await _seed_catalog()
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "catalog_failover", True)
        first = await CatalogRoutingService.add_route(session, 1, 2, "svc-B", priority=100)
        second = await CatalogRoutingService.add_route(session, 1, 2, "svc-C", priority=50)
    assert first.id == second.id  # تحديث لا تكرار

    async with async_session_maker() as session:
        product = await session.get(Product, 1)
        routes = await CatalogRoutingService.routes_for(session, product)
    assert len(routes) == 2
    assert routes[1].provider_service_id == "svc-C"

    async with async_session_maker() as session:
        report = await CatalogRoutingService.coverage_report(session)
    assert report["products_with_failover"] >= 1


# ═══════════════════════════ مراقبة الطلبات المتوازية ═══════════════════════════


@pytest.mark.asyncio
async def test_parallel_monitoring_is_faster_and_admin_switchable(monkeypatch):
    """يقيس التسريع فعلياً بدل الاكتفاء بأن الكود يُجمَّع."""
    import services.sms_receiver_service as sr
    import tasks.order_monitor as om

    latency = 0.2
    count = 8

    async with async_session_maker() as session:
        existing = (
            await session.execute(select(NumberOrder).where(NumberOrder.id >= 900))
        ).scalars().all()
        for order in existing:
            await session.delete(order)
        await session.commit()
        for index in range(count):
            session.add(
                NumberOrder(
                    id=900 + index, user_id=1, provider=ProviderName.FIVESIM,
                    provider_order_id=f"par-{index}", service="tg", country_code="tr",
                    phone_number=f"+90555900{index:03d}",
                    price_provider_usd=Decimal("0.2"), price_sell_usd=Decimal("0.5"),
                    status=OrderStatus.PENDING,
                    expires_at=datetime.utcnow() + timedelta(minutes=5),
                )
            )
        await session.commit()

    class _Pending:
        status = "pending"
        sms_code = None
        full_text = None

    async def fake_check(provider, external_order_id):
        await asyncio.sleep(latency)
        return _Pending()

    async def noop_countdown(bot, order):
        return None

    class FakeBot:
        def __getattr__(self, name):
            async def _noop(*args, **kwargs):
                return None

            return _noop

    monkeypatch.setattr(sr.SMSReceiverService, "check", staticmethod(fake_check))
    monkeypatch.setattr(om, "_update_countdown", noop_countdown)

    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "instant_delivery", False)
    start = time.monotonic()
    await om.check_pending_orders(FakeBot())
    sequential = time.monotonic() - start

    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "instant_delivery", True)
        await FeatureService.set_option(session, "instant_delivery", "batch_size", 25)
    start = time.monotonic()
    await om.check_pending_orders(FakeBot())
    parallel = time.monotonic() - start

    assert sequential >= count * latency * 0.9
    assert parallel < sequential * 0.5

    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "instant_delivery", False)
        assert await om._batch_size() == 1
        await FeatureService.set_enabled(session, "instant_delivery", True)
        assert await om._batch_size() == 25


# ═══════════════════════════ دورة حياة الاشتراكات ═══════════════════════════


@pytest.mark.asyncio
async def test_subscription_requires_enabled_feature():
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "subscription_lifecycle", False)
        with pytest.raises(SubscriptionError):
            await SubscriptionLifecycleService.create(session, 1, 1, days=30)
        await FeatureService.set_enabled(session, "subscription_lifecycle", True)


@pytest.mark.asyncio
async def test_subscription_tracks_expiry_and_reports_mrr():
    await _seed_catalog()
    await _ensure_plan()
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "subscription_lifecycle", True)
        subscription = await SubscriptionLifecycleService.create(
            session, 501, 1, days=30, auto_renew=True, plan_id=1
        )
        assert subscription.expires_at > datetime.utcnow()

    async with async_session_maker() as session:
        active = await SubscriptionLifecycleService.active_for(session, 501)
        assert len(active) == 1

    async with async_session_maker() as session:
        report = await SubscriptionLifecycleService.mrr_report(session)
    assert report["active_subscriptions"] >= 1
    assert report["expected_monthly_usd"] >= Decimal("9")


@pytest.mark.asyncio
async def test_expiring_subscription_reminds_once_per_day_level():
    await _seed_catalog()
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "subscription_lifecycle", True)
        subscription = await SubscriptionLifecycleService.create(
            session, 501, 1, days=30, auto_renew=True
        )
        subscription.expires_at = datetime.utcnow() + timedelta(days=2)
        await session.commit()
        sub_id = subscription.id

    async with async_session_maker() as session:
        stats = await SubscriptionLifecycleService.run_cycle(session, bot=None)
    assert stats["reminded"] >= 1

    # دورة ثانية في نفس اليوم يجب ألا تكرر التنبيه
    async with async_session_maker() as session:
        stats = await SubscriptionLifecycleService.run_cycle(session, bot=None)
    assert stats["reminded"] == 0


@pytest.mark.asyncio
async def test_auto_renew_extends_expiry_and_charges_the_user():
    await _seed_catalog()
    await _ensure_plan()
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "subscription_lifecycle", True)
        subscription = await SubscriptionLifecycleService.create(
            session, 501, 1, days=30, auto_renew=True, plan_id=1
        )
        subscription.expires_at = datetime.utcnow() - timedelta(hours=1)
        await session.commit()

    before = Decimal("0")
    async with async_session_maker() as session:
        user = await session.get(User, 501)
        before = user.balance

    async with async_session_maker() as session:
        stats = await SubscriptionLifecycleService.run_cycle(session, bot=None)
    assert stats["renewed"] >= 1

    # run_cycle يجدد كل الاشتراكات المستحقة لا هذا الاشتراك وحده،
    # لذا الخصم المتوقع = عدد المجدَّد × سعر الخطة.
    async with async_session_maker() as session:
        user = await session.get(User, 501)
        expected = before - (Decimal("9") * stats["renewed"])
        assert user.balance == expected, (
            f"balance {user.balance} != {expected} "
            f"(before={before}, renewed={stats['renewed']})"
        )


@pytest.mark.asyncio
async def test_cancelled_subscription_is_not_renewed():
    await _seed_catalog()
    await _ensure_plan()
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "subscription_lifecycle", True)
        subscription = await SubscriptionLifecycleService.create(
            session, 501, 1, days=30, auto_renew=True, plan_id=1
        )
        sub_id = subscription.id
        assert await SubscriptionLifecycleService.cancel(session, sub_id, 501) is True

        sub = await session.get(UserSubscription, sub_id)
        sub.expires_at = datetime.utcnow() - timedelta(hours=1)
        await session.commit()

    before = Decimal("0")
    async with async_session_maker() as session:
        before = (await session.get(User, 501)).balance

    async with async_session_maker() as session:
        stats = await SubscriptionLifecycleService.run_cycle(session, bot=None)
    assert stats["renewed"] == 0

    async with async_session_maker() as session:
        assert (await session.get(User, 501)).balance == before
