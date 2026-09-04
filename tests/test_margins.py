"""Tests for per-section / per-sub / per-product profit margins."""

from __future__ import annotations

from decimal import Decimal

from database.engine import async_session_maker
from database.models import (
    ApiProvider,
    ApiProviderType,
    Category,
    CategoryType,
    Product,
    ProductDisplayType,
    ProductFulfillmentType,
    ProductPricingType,
    ProductStatus,
    ProviderService,
    SubCategory,
)
from services.margin_service import MarginService
from services.pulled_services_service import PulledServicesService
from services.settings_service import SettingsService


async def _setup(session, cat_margin=None, sub_margin=None, inner_margin=None):
    """قسم رشق + قسم فرعي (تطبيق) + قسم داخلي + منتجان."""
    category = Category(
        name_ar="الرشق",
        emoji="📈",
        type=CategoryType.SMM,
        is_active=True,
        profit_margin_percent=cat_margin,
    )
    session.add(category)
    await session.flush()

    app = SubCategory(
        category_id=category.id,
        name_ar="إنستقرام",
        emoji="📷",
        is_active=True,
        profit_margin_percent=sub_margin,
    )
    session.add(app)
    await session.flush()

    inner = SubCategory(
        category_id=category.id,
        parent_sub_category_id=app.id,
        name_ar="متابعون",
        emoji="👥",
        kind_key="followers",
        is_active=True,
        profit_margin_percent=inner_margin,
    )
    session.add(inner)
    await session.flush()

    def make_product(sub_id, cost, price, own_margin=None):
        return Product(
            sub_category_id=sub_id,
            name_ar="منتج",
            price_usd=price,
            cost_price_usd=cost,
            pricing_type=ProductPricingType.MARGIN_PERCENT,
            profit_margin_percent=own_margin,
            display_type=ProductDisplayType.PER_1000,
            min_quantity=100,
            max_quantity=10000,
            requires_quantity=True,
            fulfillment_type=ProductFulfillmentType.API,
            status=ProductStatus.ACTIVE,
        )

    p1 = make_product(app.id, Decimal("10"), Decimal("15"))
    p2 = make_product(inner.id, Decimal("10"), Decimal("15"))
    session.add_all([p1, p2])
    await session.commit()
    return category, app, inner, p1, p2


async def test_resolve_hierarchy():
    async with async_session_maker() as session:
        category, app, inner, p1, p2 = await _setup(session)

        # عالمي افتراضياً (50%)
        percent, source = await MarginService.resolve_product_margin(session, p1)
        assert (percent, source) == (Decimal("50"), "عالمي")

        # هامش القسم
        category.profit_margin_percent = Decimal("60")
        await session.commit()
        percent, source = await MarginService.resolve_product_margin(session, p1)
        assert percent == Decimal("60")
        assert source.startswith("قسم")

        # هامش القسم الفرعي (تطبيق)
        app.profit_margin_percent = Decimal("70")
        await session.commit()
        percent, source = await MarginService.resolve_product_margin(session, p1)
        assert percent == Decimal("70")
        assert source.startswith("قسم فرعي")

        # هامش القسم الداخلي (متابعون) — أعلى في السلسلة
        inner.profit_margin_percent = Decimal("80")
        await session.commit()
        percent, source = await MarginService.resolve_product_margin(session, p2)
        assert percent == Decimal("80")

        # هامش المنتج اليدوي له الأولوية دائماً (margin_manual=True يحفظها الأدمن)
        p1.profit_margin_percent = Decimal("90")
        p1.margin_manual = True
        await session.commit()
        percent, source = await MarginService.resolve_product_margin(session, p1)
        assert (percent, source) == (Decimal("90"), "منتج")


async def test_cascade_category():
    async with async_session_maker() as session:
        category, app, inner, p1, p2 = await _setup(session)

        # البداية: بلا أي هامش → العالمى 50% → السعر 15 (10 × 1.5)
        assert p1.price_usd == Decimal("15")

        # ضبط هامش القسم 100% → 10 × 2 = 20 للمنتجات بلا هامش خاص
        updated = await MarginService.set_category_margin(session, category, Decimal("100"))
        assert updated == 2
        assert p1.price_usd == Decimal("20.0000")
        assert p2.price_usd == Decimal("20.0000")

        # منتج بهامش خاص لا يتأثر (الخدمة تعيد حسابه من هامشه)
        await MarginService.set_product_margin(session, p1, Decimal("50"))
        assert p1.price_usd == Decimal("15.0000")
        updated = await MarginService.set_category_margin(session, category, Decimal("200"))
        assert updated == 1  # فقط p2
        assert p1.price_usd == Decimal("15.0000")  # 50% خاص
        assert p2.price_usd == Decimal("30.0000")  # 200% من القسم


async def test_cascade_subtree_only_its_sub():
    async with async_session_maker() as session:
        _category, app, inner, p1, p2 = await _setup(session)

        # هامش على التطبيق (إنستقرام) يشمل أقسامه الداخلية
        updated = await MarginService.set_sub_margin(session, app, Decimal("100"))
        assert updated == 2
        assert p1.price_usd == Decimal("20.0000")
        assert p2.price_usd == Decimal("20.0000")


async def test_set_product_margin_and_clear():
    async with async_session_maker() as session:
        _category, _app, _inner, p1, _p2 = await _setup(session)

        # السعر الحالي 15 = 10 × 1.5؛ ضبط 50% لا يغيّره (نفس القيمة)
        changed = await MarginService.set_product_margin(session, p1, Decimal("50"))
        assert not changed
        assert p1.price_usd == Decimal("15.0000")

        # رفع هامش المنتج → 100% → 20
        changed = await MarginService.set_product_margin(session, p1, Decimal("100"))
        assert changed
        assert p1.price_usd == Decimal("20.0000")

        # مسح الهامش الخاص → يرجع للهامش الفعّال الأعلى (عالمي 50%)
        changed = await MarginService.set_product_margin(session, p1, None)
        assert p1.profit_margin_percent is None
        assert p1.price_usd == Decimal("15.0000")


async def test_implicit_margin_for_rashi():
    # التكلفة 10 والبيع 15 → هامش ضمني 50%
    implicit = MarginService.implicit_margin(Decimal("10"), Decimal("15"))
    assert implicit == Decimal("50.00")

    # بلا تكلفة → لا هامش ضمني
    assert MarginService.implicit_margin(Decimal("0"), Decimal("15")) is None
    assert MarginService.implicit_margin(Decimal("10"), Decimal("0")) is None


async def test_publish_rashi_service_stores_margin():
    async with async_session_maker() as session:
        provider = ApiProvider(
            name="Test SMM",
            type=ApiProviderType.SMM,
            api_url="https://example.com/api",
            api_key="k",
        )
        session.add(provider)
        await session.flush()
        service = ProviderService(
            api_provider_id=provider.id,
            external_service_id="9001",
            name="1000 متابع",
            rate=Decimal("10"),
            rate_usd=Decimal("10"),
            min_quantity=100,
            max_quantity=10000,
            requires_quantity=True,
        )
        session.add(service)
        await session.flush()

        _category, app, _inner, _p1, _p2 = await _setup(session)

        product = await PulledServicesService.publish(
            session, service, app.id, sell_price=Decimal("15")
        )
        # الهامش الضمني أصبح محفوظاً وظاهراً
        assert product.profit_margin_percent == Decimal("50.00")
        assert product.pricing_type == ProductPricingType.MARGIN_PERCENT
        assert product.price_usd == Decimal("15")  # السعر يدوي لم يتغير

        # رفع هامش القسم → الهامش الضمني ليس يدوياً، فهامش القسم يتحكم به
        # (هذا هو السلوك المطلوب: تتحكم بهامش كل قسم وكل قسم فرعي).
        category = await session.get(Category, _category.id)
        updated = await MarginService.set_category_margin(session, category, Decimal("100"))
        assert updated >= 1
        assert product.price_usd == Decimal("20.0000")

        # المنتج ما زال غير يدوي → يقبل التحكم من هامش قسمه.
        assert product.margin_manual is False


async def test_global_margin_setting_applies_to_plain_products():
    async with async_session_maker() as session:
        _category, _app, _inner, p1, _p2 = await _setup(session)

        # تغيير الهامش العالمي لا يعيد الحساب تلقائياً (يُطبق عند النشر/الضبط)
        await SettingsService.set(session, "default_profit_margin_percent", "80")
        percent, source = await MarginService.resolve_product_margin(session, p1)
        assert (percent, source) == (Decimal("80"), "عالمي")
        # إعادة الحساب الصريحة تطبقه
        changed = await MarginService.recalc_product_from_margin(session, p1, percent)
        assert changed
        assert p1.price_usd == Decimal("18.0000")
