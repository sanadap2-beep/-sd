"""
اختبارات مزامنة الاشتراكات الرقمية (ggsoma Partner API).

- الكتالوج يُنشر تلقائياً في قسم الاشتراكات: قسم فرعي لكل علامة/تطبيق.
- سعر البيع = yourPrice + هامش الربح (افتراضياً 23%)، والمتوفر فقط يظهر.
- إعادة المزامنة لا تكرر شيئاً؛ تسعّر منتجاتها تلقائياً عند تغيّر التكلفة،
  وتحدّث التوفر (تعطيل نفاد المخزون وتفعيل عودته) دون لمس المنتجات اليدوية.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    ApiProtocolType,
    ApiProvider,
    ApiProviderType,
    Category,
    CategoryType,
    Product,
    ProductStatus,
    ProviderService,
    SubCategory,
)
from protocols.ggsoma_v1 import GgsomaPartnerProtocol
from services.feature_service import FeatureService
from services.subscriptions_sync_service import SubscriptionsSyncService

pytestmark = pytest.mark.asyncio


def _product(
    pid: int,
    slug: str,
    name: str,
    your_price: str,
    provider_key: str = "gemini",
    in_stock: bool = True,
    sort: int = 1,
) -> dict:
    return {
        "id": pid,
        "slug": slug,
        "productCode": f"G-{pid}",
        "name": name,
        "provider": {
            "id": 3,
            "key": provider_key,
            "name": "Gemini" if provider_key == "gemini" else "CapCut",
            "emoji": {"normal": "✨" if provider_key == "gemini" else "🎬"},
        },
        "deliveryType": "LINK" if provider_key == "gemini" else "COUPON",
        "sortOrder": sort,
        "catalogPrice": your_price,
        "yourPrice": your_price,
        "currency": "USD",
        "durationDays": 30,
        "stock": {"inStock": in_stock, "count": 48 if in_stock else 0, "maxQuantity": 48},
        "flags": {"instantDelivery": True},
    }


async def _add_provider(session, name: str = "ggsoma") -> ApiProvider:
    provider = ApiProvider(
        name=name,
        type=ApiProviderType.SUBSCRIPTIONS,
        protocol_type=ApiProtocolType.CUSTOM,
        api_url="https://ggsoma.store/api/partner/v1",
        api_key="sk_live_test123",
        custom_config=json.dumps({"engine": "ggsoma"}),
        is_active=True,
    )
    session.add(provider)
    await session.commit()
    await session.refresh(provider)
    return provider


def _fake_catalog(products: list[dict], providers: list[dict] | None = None):
    providers = providers or [
        {"id": 3, "key": "gemini", "name": "Gemini", "emoji": {"normal": "✨"}},
        {"id": 5, "key": "capcut", "name": "CapCut", "emoji": {"normal": "🎬"}},
    ]

    async def fake_providers(self):
        return providers

    proto = GgsomaPartnerProtocol(api_url="x", api_key="y")

    async def fake_services(self, service_type=None, category=None):
        items = [
            item
            for item in products
            if not service_type or str(item["provider"]["key"]) == service_type
        ]
        services = []
        for item in items:
            svc = proto._parse_product(item)
            if svc is not None:
                services.append(svc)
        return services

    return fake_providers, fake_services


async def _sections(session) -> dict[str, SubCategory]:
    category = (
        await session.execute(
            select(Category).where(Category.type == CategoryType.SUBSCRIPTIONS)
        )
    ).scalar_one()
    result = await session.execute(
        select(SubCategory).where(
            SubCategory.category_id == category.id,
            SubCategory.parent_sub_category_id.is_(None),
        )
    )
    return {s.name_ar: s for s in result.scalars().all()}


async def _products_in(session, sub_id: int) -> list[Product]:
    return list(
        (
            await session.execute(
                select(Product).where(Product.sub_category_id == sub_id)
            )
        )
        .scalars()
        .all()
    )


async def _setup_basic():
    """مزود + كتالوج: منتجان جاهزان في Gemini + منتج نفد في CapCut."""
    products = [
        _product(42, "gemini-pro-monthly", "Gemini Pro — 30 days", "12.50"),
        _product(43, "gemini-basic-monthly", "Gemini Basic — 30 days", "7.00", sort=2),
        _product(99, "capcut-pro-coupon", "CapCut Pro — كوبون", "8.00", provider_key="capcut", in_stock=False),
    ]
    async with async_session_maker() as session:
        provider = await _add_provider(session)
        # مسح أي قيود تخزين قديمة في FeatureService حتى لا تؤثر على التوقع.
        return provider, products


async def test_sync_publishes_catalog_with_margin_and_stock(monkeypatch):
    provider, products = await _setup_basic()
    fake_providers, fake_services = _fake_catalog(products)
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_providers", fake_providers)
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_services", fake_services)

    async with async_session_maker() as session:
        report = await SubscriptionsSyncService.sync_provider(session, provider)

    assert report["sections_created"] == 2
    assert report["apps"] == 2
    assert report["products_created"] == 3
    assert report["products_deactivated"] == 0  # من البداية معطَّل
    assert report["errors"] == 0

    async with async_session_maker() as session:
        sections = await _sections(session)
        # أقسام البذرة العامة موجودة سلفاً (ذكاء اصطناعي…) — أقسامنا أُضيفت بجانبها.
        assert {"Gemini", "CapCut"} <= set(sections)
        gemini = await _products_in(session, sections["Gemini"].id)
        capcut = await _products_in(session, sections["CapCut"].id)

        by_name = {p.name_ar: p for p in gemini}
        pro = by_name["Gemini Pro — 30 days"]
        # هامش 23%: 12.5 × 1.23 = 15.375 → 15.38
        assert pro.price_usd == Decimal("15.38")
        assert pro.cost_price_usd == Decimal("12.50")
        assert pro.status == ProductStatus.ACTIVE
        assert pro.is_auto_published is True
        assert pro.provider_service_id == "gemini-pro-monthly"
        assert pro.display_type.value == "fixed_total"

        basic = by_name["Gemini Basic — 30 days"]
        assert basic.price_usd == Decimal("8.61")  # 7 × 1.23 = 8.61
        assert basic.sort_order == 2

        # خارج المخزون → غير منشور للبيع
        assert len(capcut) == 1
        assert capcut[0].status == ProductStatus.INACTIVE


async def test_resync_is_idempotent_and_reprices_auto_products(monkeypatch):
    provider, products = await _setup_basic()
    fake_providers, fake_services = _fake_catalog(products)
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_providers", fake_providers)
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_services", fake_services)

    async with async_session_maker() as session:
        await SubscriptionsSyncService.sync_provider(session, provider)
        report2 = await SubscriptionsSyncService.sync_provider(session, provider)

    assert report2["sections_created"] == 0
    assert report2["products_created"] == 0
    assert report2["products_repriced"] == 0  # السعر لم يتغير
    assert report2["products_deactivated"] == 0
    assert report2["errors"] == 0

    # غيّر المزود سعر Gemini Pro: 12.5 → 13.00 فيُعاد التسعير 15.99
    for item in products:
        if item["slug"] == "gemini-pro-monthly":
            item["yourPrice"] = "13.00"
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_services", fake_services)

    async with async_session_maker() as session:
        report3 = await SubscriptionsSyncService.sync_provider(session, provider)
        assert report3["products_repriced"] == 1

    async with async_session_maker() as session:
        sections = await _sections(session)
        pro = [
            p
            for p in await _products_in(session, sections["Gemini"].id)
            if p.name_ar == "Gemini Pro — 30 days"
        ][0]
        assert pro.price_usd == Decimal("15.99")  # 13 × 1.23


async def test_sync_toggles_availability_and_keeps_manual_products(monkeypatch):
    provider, products = await _setup_basic()
    fake_providers, fake_services = _fake_catalog(products)
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_providers", fake_providers)
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_services", fake_services)

    async with async_session_maker() as session:
        await SubscriptionsSyncService.sync_provider(session, provider)

        # منتج يدوي (الأدمن نشره بنفسه من نفس الخدمة في نفس القسم) —
        # «توأم» لمنتجنا التلقائي: يجب أن يبقى كما هو وبسعره مهما حدث.
        sections = await _sections(session)
        svc = (
            await session.execute(
                select(ProviderService).where(
                    ProviderService.external_service_id == "gemini-basic-monthly"
                )
            )
        ).scalar_one()
        manual = Product(
            sub_category_id=sections["Gemini"].id,
            api_provider_id=provider.id,
            provider_service_ref_id=svc.id,
            provider_service_id="gemini-basic-monthly",
            name_ar="عرضي الخاص لـ Gemini Basic",
            price_usd=Decimal("9.99"),
            cost_price_usd=Decimal("7.00"),
            is_auto_published=False,
        )
        session.add(manual)
        await session.commit()

        # نفد مخزون Gemini Pro + تغيّر سعر Gemini Basic عند المزود (7 → 13).
        for item in products:
            if item["slug"] == "gemini-pro-monthly":
                item["stock"] = {"inStock": False, "count": 0, "maxQuantity": 0}
            if item["slug"] == "gemini-basic-monthly":
                item["yourPrice"] = "13.00"
        report = await SubscriptionsSyncService.sync_provider(session, provider)

        assert report["products_deactivated"] >= 1  # Pro عُطّل (نفد)
        assert report["products_repriced"] >= 1  # منتجنا التلقائي لـ Basic أُعيد تسعيره

    async with async_session_maker() as session:
        sections = await _sections(session)
        gemini = await _products_in(session, sections["Gemini"].id)
        auto_basic = next(p for p in gemini if p.name_ar == "Gemini Basic — 30 days")
        manual_now = next(p for p in gemini if p.name_ar == "عرضي الخاص لـ Gemini Basic")
        pro = next(p for p in gemini if p.name_ar == "Gemini Pro — 30 days")
        assert pro.status == ProductStatus.INACTIVE
        # منتجنا التلقائي أُعيد تسعيره بالهامش (13 × 1.23 = 15.99)
        assert auto_basic.price_usd == Decimal("15.99")
        # المنتج اليدوي بقي كما هو تماماً — لا إعادة تسعير ولا تفعيل/تعطيل.
        assert manual_now.status == ProductStatus.ACTIVE
        assert manual_now.price_usd == Decimal("9.99")
        assert manual_now.is_auto_published is False

        # رجع المخزون → يُعاد تفعيل منتجنا فقط.
        for item in products:
            if item["slug"] == "gemini-pro-monthly":
                item["stock"] = {"inStock": True, "count": 10, "maxQuantity": 10}
        async with async_session_maker() as session2:
            report2 = await SubscriptionsSyncService.sync_provider(session2, provider)
        assert report2["products_reactivated"] >= 1
        assert report2["skipped_manual"] == 0


async def test_sync_skips_creating_duplicate_when_only_manual_covers_service(
    monkeypatch,
):
    """خدمة يغطيها منتج يدوي فقط → لا ننشئ نسخة تلقائية بجانبه أبداً."""
    provider, products = await _setup_basic()
    fake_providers, fake_services = _fake_catalog(products)
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_providers", fake_providers)
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_services", fake_services)

    async with async_session_maker() as session:
        await SubscriptionsSyncService.sync_provider(session, provider)

        # استبدل الأدمن منتجنا التلقائي لمنتج CapCut بمنتجه اليدوي.
        sections = await _sections(session)
        svc = (
            await session.execute(
                select(ProviderService).where(
                    ProviderService.external_service_id == "capcut-pro-coupon"
                )
            )
        ).scalar_one()
        auto = (
            await session.execute(
                select(Product).where(
                    Product.provider_service_ref_id == svc.id,
                    Product.sub_category_id == sections["CapCut"].id,
                )
            )
        ).scalar_one()
        await session.delete(auto)
        manual = Product(
            sub_category_id=sections["CapCut"].id,
            api_provider_id=provider.id,
            provider_service_ref_id=svc.id,
            provider_service_id="capcut-pro-coupon",
            name_ar="عرضي الخاص لـ CapCut",
            price_usd=Decimal("5.55"),
            cost_price_usd=Decimal("8.00"),
            is_auto_published=False,
        )
        session.add(manual)
        await session.commit()

        report = await SubscriptionsSyncService.sync_provider(session, provider)

        assert report["skipped_manual"] >= 1
        assert report["products_created"] == 0

    async with async_session_maker() as session:
        sections = await _sections(session)
        capcut = await _products_in(session, sections["CapCut"].id)
        assert len(capcut) == 1  # لا توجد نسخة تلقائية مكررة
        assert capcut[0].name_ar == "عرضي الخاص لـ CapCut"
        assert capcut[0].price_usd == Decimal("5.55")


async def test_sync_all_scans_only_ggsoma_providers(monkeypatch):
    products = [_product(42, "gemini-pro-monthly", "Gemini Pro — 30 days", "12.50")]
    fake_providers, fake_services = _fake_catalog(products)
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_providers", fake_providers)
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_services", fake_services)

    async with async_session_maker() as session:
        ggsoma_provider = await _add_provider(session, name="ggsoma")
        other = ApiProvider(
            name="صديق آخر",
            type=ApiProviderType.NUMBERS,
            protocol_type=ApiProtocolType.CUSTOM,
            api_url="http://x/api/v1",
            api_key="k",
            custom_config=json.dumps({"engine": "partner_v1"}),
            is_active=True,
        )
        session.add(other)
        await session.commit()

        reports = await SubscriptionsSyncService.sync_all(session)
        # مزود واحد فقط عولج (الآخر بروتوكول مختلف)
        assert len(reports) == 1
        assert reports[0]["provider_id"] == ggsoma_provider.id


async def test_margin_option_is_configurable(monkeypatch):
    """هامش الربح يُقرأ من إعدادات الإضافة (الافتراضي 23%)."""
    assert await SubscriptionsSyncService.enabled() is True
    assert (await SubscriptionsSyncService.margin_percent()) == Decimal("23")

    async with async_session_maker() as session:
        await FeatureService.set_option(
            session, "subscriptions_auto_sync", "margin_percent", 40
        )
    assert (await SubscriptionsSyncService.margin_percent()) == Decimal("40")

    # إعادة الافتراضي حتى لا تتسرب القيمة لاختبارات أخرى في نفس العملية.
    async with async_session_maker() as session:
        await FeatureService.set_option(
            session, "subscriptions_auto_sync", "margin_percent", 23
        )
    assert (await SubscriptionsSyncService.margin_percent()) == Decimal("23")
