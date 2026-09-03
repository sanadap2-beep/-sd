"""Tests for auto-Arabization of pulled provider service names."""

from __future__ import annotations

from decimal import Decimal

from database.engine import async_session_maker
from database.models import ApiProvider, ApiProviderType, ProviderService
from services.pulled_services_service import PulledServicesService
from services.service_localization_service import (
    arabicize_service_name,
    display_service_name,
    is_arabic,
)


def test_is_arabic():
    assert is_arabic("متابعين تيك توك")
    assert is_arabic("TikTok متابعون")
    assert not is_arabic("TikTok Real Followers")
    assert not is_arabic("")


def test_arabic_passthrough():
    assert display_service_name("متابعين حقيقيين تيك توك", "تيك توك") == "متابعين حقيقيين تيك توك"
    assert display_service_name("لايكات إنستقرام سريعة", "Instagram") == "لايكات إنستقرام سريعة"


def test_arabicize_tiktok_followers():
    out = arabicize_service_name("TikTok Real Followers 1000", "TikTok", "Followers")
    assert "متابعون" in out
    assert "تيك توك" in out
    assert "1000" in out
    # لا تبقى كلمة المنصة/النوع بالإنجليزية
    assert "TikTok" not in out and "Followers" not in out


def test_arabicize_youtube_views():
    out = arabicize_service_name("YouTube Views HD 10000", "YouTube", "Views")
    assert "مشاهدات" in out
    assert "يوتيوب" in out
    assert "10000" in out


def test_arabicize_telegram_members():
    out = arabicize_service_name("Telegram Channel Members 2000", "Telegram")
    assert "أعضاء" in out
    assert "تيليجرام" in out


def test_arabicize_unknown_service_kept():
    # اسم بلا منصة/نوع معروف → يبقى كما هو (نحافظ على المعنى)
    assert arabicize_service_name("Random Service XYZ") == "Random Service XYZ"


def test_arabicize_uses_category_when_name_plain():
    # الاسم بلا منصة لكن التصنيف يحويها
    out = arabicize_service_name("Real Followers 500", "Instagram")
    assert "إنستغرام" in out
    assert "متابعون" in out


async def _provider_service(name, category=None, service_type=None, rate="10"):
    async with async_session_maker() as session:
        provider = ApiProvider(
            name="P", type=ApiProviderType.SMM, api_url="https://x", api_key="k"
        )
        session.add(provider)
        await session.flush()
        svc = ProviderService(
            api_provider_id=provider.id,
            external_service_id="9900",
            name=name,
            category=category,
            service_type=service_type,
            rate=Decimal(rate),
            rate_usd=Decimal(rate),
            min_quantity=100,
            max_quantity=10000,
            requires_quantity=True,
        )
        session.add(svc)
        await session.commit()
        await session.refresh(svc)
        return provider, svc


async def test_publish_stores_arabic_name():
    from database.models import Category, CategoryType, SubCategory

    provider, svc = await _provider_service(
        "TikTok Real Followers 1000", "TikTok", "Followers"
    )
    async with async_session_maker() as session:
        category = Category(name_ar="الرشق", emoji="📈", type=CategoryType.SMM)
        session.add(category)
        await session.flush()
        sub = SubCategory(category_id=category.id, name_ar="تيك توك", emoji="🎵")
        session.add(sub)
        await session.flush()

        product = await PulledServicesService.publish(
            session, svc, sub.id, sell_price=Decimal("15")
        )
        assert is_arabic(product.name_ar), product.name_ar
        assert "متابعون" in product.name_ar
        assert "تيك توك" in product.name_ar


async def test_publish_respects_admin_name():
    from database.models import Category, CategoryType, SubCategory

    provider, svc = await _provider_service("TikTok Real Followers 1000", "TikTok")
    async with async_session_maker() as session:
        category = Category(name_ar="الرشق", emoji="📈", type=CategoryType.SMM)
        session.add(category)
        await session.flush()
        sub = SubCategory(category_id=category.id, name_ar="تيك توك", emoji="🎵")
        session.add(sub)
        await session.flush()

        product = await PulledServicesService.publish(
            session, svc, sub.id, sell_price=Decimal("15"), name_ar="متابعين حقيقيين"
        )
        assert product.name_ar == "متابعين حقيقيين"
