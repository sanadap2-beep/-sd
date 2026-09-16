"""Tests for auto-Arabization of pulled provider service names."""

from __future__ import annotations

from decimal import Decimal

from database.engine import async_session_maker
from database.models import ApiProvider, ApiProviderType, ProviderService
from services.pulled_services_service import PulledServicesService
from services.service_localization_service import (
    arabicize_service_name,
    display_category_name,
    display_service_name,
    is_arabic,
    service_name_ar,
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


# ══════════════ تعريب منتجات المتاجر العامة (مثل Hyper Store) ══════════════


def test_arabicize_store_game_product():
    out = arabicize_service_name("Free Fire Diamonds 100")
    assert "فري فاير" in out
    assert "ماسات" in out
    assert "100" in out
    assert "Free" not in out and "Diamonds" not in out


def test_arabicize_store_game_longest_brand_first():
    out = arabicize_service_name("Free Fire Max Diamonds 300")
    assert "فري فاير ماكس" in out
    assert out.count("فري فاير") == 1


def test_arabicize_store_subscription():
    out = arabicize_service_name("Netflix Premium 1 Month")
    assert "نتفليكس" in out
    assert "مميز" in out
    assert "شهر" in out
    assert "1" in out


def test_arabicize_store_wallet():
    out = arabicize_service_name("Steam Wallet USD 20")
    assert "ستيم" in out
    assert "محفظة" in out
    assert "دولار" in out


def test_arabicize_store_gift_card_phrase():
    out = arabicize_service_name("Amazon Gift Card 25")
    assert "أمازون" in out
    assert "بطاقة هدية" in out
    assert "هدية بطاقة" not in out


def test_arabicize_store_recharge():
    out = arabicize_service_name("Vivo Cash 50 Recharge")
    assert "فيفو" in out
    assert "شحن" in out


def test_arabicize_store_ai_subscription():
    out = arabicize_service_name("Gemini Pro — 30 days")
    assert "جيميناي" in out
    assert "برو" in out
    assert "أيام" in out


def test_arabicize_store_unknown_still_kept():
    # بلا أي كلمة معروفة → الاسم الأصلي (نحمي أسماء الباقات)
    assert arabicize_service_name("Random Service XYZ") == "Random Service XYZ"


def test_display_category_name():
    assert display_category_name("Gaming") == "ألعاب"
    assert display_category_name("E-Commerce") == "تجارة إلكترونية"
    assert display_category_name("Top Up") == "شحن رصيد"
    assert display_category_name("Gift Cards") == "بطاقات هدية"
    assert display_category_name("ألعاب") == "ألعاب"  # عربي يبقى كما هو
    assert display_category_name(None) == ""
    assert display_category_name("") == ""


class _FakeService:
    def __init__(self, name, category=None, service_type=None, name_ar=None):
        self.name = name
        self.category = category
        self.service_type = service_type
        self.name_ar = name_ar


def test_service_name_ar_prefers_stored_value():
    svc = _FakeService("Free Fire Diamonds 100", "Gaming", name_ar="اسم محفوظ بالعربي")
    assert service_name_ar(svc) == "اسم محفوظ بالعربي"


def test_service_name_ar_falls_back_for_legacy_rows():
    # سجل قديم بلا name_ar → تعريب على الطايرة من الاسم الأصلي
    svc = _FakeService("Free Fire Diamonds 100", "Gaming")
    assert service_name_ar(svc) == arabicize_service_name(
        "Free Fire Diamonds 100", "Gaming"
    )


async def test_pull_then_publish_any_product_with_info_and_margin(monkeypatch):
    """سيناريو كامل: سحب خدمات Hyper Store (بتعريب) ثم نشر أي منتج
    في أي قسم مع معلوماته ونسبة ربحه المستمدة من (سعر البيع/التكلفة)."""
    from decimal import Decimal

    from database.models import Category, CategoryType, SubCategory
    from protocols.base import ProtocolService
    from protocols.factory import ProtocolFactory
    from services.provider_sync_service import ProviderSyncService

    class FakeHyper:
        async def get_balance(self):
            from protocols.base import ProtocolBalance

            return ProtocolBalance(Decimal("10"), "USD")

        async def get_services(self):
            return [
                ProtocolService(
                    external_id="7001",
                    name="Free Fire Diamonds 100",
                    category="Gaming",
                    rate=Decimal("1.10"),
                    min_quantity=50,
                    max_quantity=1000,
                    description="Instant delivery of diamonds.",
                    requires_link=True,
                    requires_quantity=True,
                )
            ]

    monkeypatch.setattr(
        ProtocolFactory, "create_from_provider", lambda provider: FakeHyper()
    )
    async with async_session_maker() as session:
        provider = ApiProvider(
            name="Hyper Store",
            type=ApiProviderType.STORE,
            api_url="https://api.hyper4store.com",
            api_key="token",
            currency="USD",
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        provider_id = provider.id
        # قسم الأدمن الذي سيستقبل المنتج
        category = Category(name_ar="ألعابي", emoji="🎮", type=CategoryType.CUSTOM)
        session.add(category)
        await session.flush()
        sub = SubCategory(category_id=category.id, name_ar="فري فاير", emoji="🔥")
        session.add(sub)
        await session.commit()
        sub_id = sub.id

    result = await ProviderSyncService.sync_provider_services(provider_id)
    assert result.success and result.new_services == 1

    async with async_session_maker() as session:
        from sqlalchemy import select

        svc = (
            await session.execute(
                select(ProviderService).where(
                    ProviderService.api_provider_id == provider_id
                )
            )
        ).scalar_one()
        assert is_arabic(svc.name_ar)

        # نشر المنتج في قسم الأدمن بسعر بيع أعلى من التكلفة (ربح 100%)
        product = await PulledServicesService.publish(
            session, svc, sub_id, sell_price=Decimal("2.20")
        )

    assert is_arabic(product.name_ar), product.name_ar
    assert "فري فاير" in product.name_ar and "ماسات" in product.name_ar
    # معلومات الخدمة تنتقل للمنتج
    assert product.cost_price_usd == Decimal("1.10")
    assert product.price_usd == Decimal("2.20")
    assert product.min_quantity == 50
    assert product.max_quantity == 1000
    assert product.requires_link is True
    assert product.requires_quantity is True
    assert product.description == "Instant delivery of diamonds."
    # نسبة الربح محفوظة (2.20 مقابل 1.10 = ربح 100%)
    assert product.profit_margin_percent == Decimal("100.00")


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


# ══════════════ أسماء شحن الألعاب: العلامة التجارية أولاً ══════════════


def test_arabicize_store_brand_wins_over_smm_kind():
    """«Google Play» ليست «مشاهدات»: ``play`` من أسماء نوع المشاهدات.

    قبل الإصلاح كان الناتج: «مشاهدات Google هدية Card USD (25)».
    """
    out = arabicize_service_name("Google Play Gift Card 25 USD", "Cards")
    assert "جوجل بلاي" in out
    assert "بطاقة هدية" in out
    assert "دولار" in out
    assert "مشاهدات" not in out
    assert "Google" not in out and "Card" not in out


def test_arabicize_games_topup_names():
    assert "ببجي موبايل" in arabicize_service_name("PUBG Mobile 60 UC", "Games")
    assert "فري فاير" in arabicize_service_name("Free Fire 100 Diamonds", "Games")
    assert "موبايل ليجندز" in arabicize_service_name(
        "Mobile Legends 86 Diamonds", "Games"
    )
    # «Mobile» المتبقية بعد العلامة التجارية تُترجم هي الأخرى
    cod = arabicize_service_name("Call of Duty Mobile 80 CP", "Games")
    assert "كول أوف ديوتي" in cod
    assert "موبايل" in cod
    assert "Mobile" not in cod


def test_arabicize_smm_kind_still_wins_without_brand():
    """أسماء الرشق الحقيقية تبقى على مسار SMM (العلامة التجارية غير موجودة)."""
    assert arabicize_service_name("YouTube Views 10000", "YouTube") == "مشاهدات يوتيوب (10000)"
    assert arabicize_service_name("TikTok Plays 5000", "TikTok") == "مشاهدات تيك توك (5000)"
