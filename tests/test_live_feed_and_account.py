"""
اختبارات: «مباشر البوت» (إشعارات الشراء والاسترجاع) + «إجمالي مشترياتك».

- إجمالي المشتريات المعروض للمستخدم يحسب فقط الطلبات التي اكتملت
  وتفعّلت فعلاً (لا المعلّقة ولا المسترجَعة).
- إضافة live_bot_feed مسجّلة بمفاتيحها، والبوابات تُحترم عند التبديل.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    NumberOrder,
    OrderStatus,
    Product,
    ProductDisplayType,
    ProductPricingType,
    ProductStatus,
    ProviderName,
    SubCategory,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from services.feature_registry import get_spec
from services.feature_service import FeatureService
from services.ledger_service import LedgerService


async def _user(user_id: int) -> User:
    async with async_session_maker() as session:
        user = User(
            telegram_id=user_id,
            username=f"u{user_id}",
            full_name=f"U{user_id}",
            balance=Decimal("100"),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _product() -> int:
    """منتج حقيقي لأن unified_orders.product_id مفتاح أجنبي إجباري."""
    async with async_session_maker() as session:
        existing = await session.get(Product, 1)
        if existing is not None:
            return existing.id
        category = Category(name_ar="رشق", emoji="📈", type=CategoryType.SMM, is_active=True)
        session.add(category)
        await session.flush()
        sub = SubCategory(
            category_id=category.id, name_ar="إنستغرام", emoji="📸", is_active=True
        )
        session.add(sub)
        await session.flush()
        product = Product(
            id=1,
            sub_category_id=sub.id,
            name_ar="1000 متابع",
            price_usd=Decimal("10.00"),
            cost_price_usd=Decimal("5"),
            pricing_type=ProductPricingType.FIXED,
            display_type=ProductDisplayType.PER_1000,
            status=ProductStatus.ACTIVE,
            min_quantity=1,
            max_quantity=100000,
        )
        session.add(product)
        await session.commit()
        return product.id


@pytest.mark.asyncio
async def test_realized_totals_count_only_completed_orders():
    """بطاقة الحساب: المعلق/المسترجَع لا يدخل «إجمالي مشترياتك»."""
    user = await _user(9101)
    product_id = await _product()

    async with async_session_maker() as session:
        # رقم تفعّل فعلاً → يُحسب.
        session.add(
            NumberOrder(
                user_id=user.id,
                provider=ProviderName.FIVESIM,
                provider_order_id="rt-1",
                service="tg",
                country_code="tr",
                phone_number="+905555911000",
                price_provider_usd=Decimal("0.30"),
                price_sell_usd=Decimal("1.00"),
                status=OrderStatus.COMPLETED,
            )
        )
        # رقم انتهت صلاحيته ورجع رصيده → لا يُحسب.
        session.add(
            NumberOrder(
                user_id=user.id,
                provider=ProviderName.FIVESIM,
                provider_order_id="rt-2",
                service="tg",
                country_code="tr",
                phone_number="+905555912000",
                price_provider_usd=Decimal("0.30"),
                price_sell_usd=Decimal("2.00"),
                status=OrderStatus.REFUNDED,
            )
        )
        # رقم ما زال معلقاً بانتظار التفعيل → لا يُحسب.
        session.add(
            NumberOrder(
                user_id=user.id,
                provider=ProviderName.FIVESIM,
                provider_order_id="rt-3",
                service="tg",
                country_code="tr",
                phone_number="+905555913000",
                price_provider_usd=Decimal("0.30"),
                price_sell_usd=Decimal("3.00"),
                status=OrderStatus.PENDING,
            )
        )
        # طلب رشق مكتمل → يُحسب.
        session.add(
            UnifiedOrder(
                user_id=user.id,
                product_id=product_id,
                api_provider_id=None,
                quantity=1000,
                price_usd=Decimal("4.00"),
                cost_price_usd=Decimal("2.00"),
                status=UnifiedOrderStatus.COMPLETED,
            )
        )
        # طلب رشق منجز جزئياً (رجع باقيه) → يُحسب بقيمته.
        session.add(
            UnifiedOrder(
                user_id=user.id,
                product_id=product_id,
                api_provider_id=None,
                quantity=1000,
                price_usd=Decimal("5.00"),
                cost_price_usd=Decimal("2.50"),
                status=UnifiedOrderStatus.PARTIAL,
                remains=500,
            )
        )
        # طلب قيد التنفيذ → لا يُحسب بعد.
        session.add(
            UnifiedOrder(
                user_id=user.id,
                product_id=product_id,
                api_provider_id=None,
                quantity=1000,
                price_usd=Decimal("6.00"),
                cost_price_usd=Decimal("3.00"),
                status=UnifiedOrderStatus.PROCESSING,
            )
        )
        # طلب فشل ورجع رصيده → لا يُحسب.
        session.add(
            UnifiedOrder(
                user_id=user.id,
                product_id=product_id,
                api_provider_id=None,
                quantity=1000,
                price_usd=Decimal("7.00"),
                cost_price_usd=Decimal("3.50"),
                status=UnifiedOrderStatus.REFUNDED,
            )
        )
        await session.commit()

        spent, count = await LedgerService.user_realized_totals(session, user.id)

    assert count == 3  # رقم مفعّل + رشق مكتمل + جزئي
    assert spent == Decimal("10.00")  # 1 + 4 + 5
    # تأكيد أن عمود total_spent_usd القديم (الدفع المسبق) مختلف — هذا هو
    # جوهر الشكوى: كان يجمع المعلّق/المسترجَع أيضاً.
    assert spent != user.total_spent_usd


@pytest.mark.asyncio
async def test_live_bot_feed_registered_and_gated():
    """إضافة «مباشر البوت» مسجّلة، مفعّلة افتراضياً، وبواباتها تعمل."""
    spec = get_spec("live_bot_feed")
    assert spec is not None
    assert spec.default_enabled is True
    assert spec.defaults.get("notify_success") is True
    assert spec.defaults.get("notify_refund") is True

    # الإعداد الافتراضي يُقرأ من السجل حتى قبل إنشاء صف في القاعدة.
    assert await FeatureService.enabled("live_bot_feed") is True
    assert await FeatureService.config_bool("live_bot_feed", "notify_success") is True
    assert await FeatureService.config_bool("live_bot_feed", "notify_refund") is True

    # إيقاف نوع واحد لا يوقف الآخر.
    async with async_session_maker() as session:
        await FeatureService.set_option(
            session, "live_bot_feed", "notify_refund", False
        )
    assert await FeatureService.config_bool("live_bot_feed", "notify_success") is True
    assert await FeatureService.config_bool("live_bot_feed", "notify_refund") is False

    # إيقاف الإضافة كلها يوقف كل الأنواع.
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "live_bot_feed", False)
    assert await FeatureService.enabled("live_bot_feed") is False

    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "live_bot_feed", True)
        await FeatureService.set_option(
            session, "live_bot_feed", "notify_refund", True
        )
