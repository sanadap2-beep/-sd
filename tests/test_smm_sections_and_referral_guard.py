"""اختبارات: الأقسام الداخلية لقسم الرشق + البناء التلقائي + حماية الإحالة."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from database.engine import async_session_maker
from database.models import (
    AbuseEvent,
    ApiProvider,
    ApiProviderType,
    Category,
    CategoryType,
    Product,
    ProductStatus,
    ProviderService,
    ProviderServiceStatus,
    SubCategory,
    User,
)
from services.dynamic_service import DynamicService
from services.referral_guard_service import ReferralGuardService
from services.smm_catalog import SHORT_TO_PLATFORM
from services.smm_sections_service import SmmSectionsService

pytestmark = pytest.mark.asyncio


async def _add_provider(session, name: str = "TestSMM") -> ApiProvider:
    provider = ApiProvider(
        name=name,
        type=ApiProviderType.SMM,
        api_url="https://example.test/api",
        api_key="k",
        is_active=True,
    )
    session.add(provider)
    await session.commit()
    await session.refresh(provider)
    return provider


async def _add_service(
    session,
    provider: ApiProvider,
    name: str,
    category: str | None,
    rate: str,
    external_id: str | None = None,
) -> ProviderService:
    service = ProviderService(
        api_provider_id=provider.id,
        external_service_id=external_id or f"svc-{name[:16]}-{rate}",
        name=name,
        category=category or name,
        rate=Decimal(rate),
        rate_usd=Decimal(rate),
        min_quantity=100,
        max_quantity=100000,
        requires_link=True,
        requires_quantity=True,
        status=ProviderServiceStatus.ACTIVE,
    )
    session.add(service)
    await session.commit()
    await session.refresh(service)
    return service


async def _smm_category(session) -> Category:
    return (
        await session.execute(
            select(Category).where(Category.type == CategoryType.SMM)
        )
    ).scalar_one()


async def _products_in(session, sub_id: int) -> list[Product]:
    return list(
        (
            await session.execute(
                select(Product).where(Product.sub_category_id == sub_id)
            )
        ).scalars().all()
    )


# ═══════════════ البناء التلقائي ═══════════════


async def test_builder_creates_sections_and_publishes_cheapest():
    async with async_session_maker() as session:
        provider = await _add_provider(session)

        # إنستغرام: 12 خدمة متابعين (ننشر أرخص 10 فقط) + خدمتا لايكات.
        followers = []
        rates = (
            "0.90", "0.70", "0.60", "0.80", "0.50", "1.00",
            "0.95", "0.75", "0.65", "0.85", "0.55", "1.10",
        )
        for index, rate in enumerate(rates):
            followers.append(
                await _add_service(
                    session,
                    provider,
                    name=f"Instagram Followers {index}",
                    category="Instagram Followers",
                    rate=rate,
                    external_id=f"ig-f-{index}",
                )
            )
        likes = [
            await _add_service(
                session,
                provider,
                name="Instagram Likes 1",
                category="Instagram Likes",
                rate="0.30",
                external_id="ig-l-1",
            ),
            await _add_service(
                session,
                provider,
                name="Instagram Likes 2",
                category="Instagram Likes",
                rate="0.20",
                external_id="ig-l-2",
            ),
        ]

        report = await SmmSectionsService.build(session)

        # الأقسام الداخلية وُجدت/أُنشئت تحت تطبيق إنستغرام.
        smm_cat = await _smm_category(session)
        instagram = None
        for app in await DynamicService.get_active_root_sub_categories(session, smm_cat.id):
            if app.name_ar == "إنستغرام":
                instagram = app
        assert instagram is not None
        sections = await DynamicService.get_active_child_sections(session, instagram.id)
        by_kind = {section.kind_key: section for section in sections}
        assert "followers" in by_kind
        assert "likes" in by_kind
        assert report["sections_created"] >= 2
        assert report["apps"] >= 1

        # أرخص 10 متابعين + لايكتان فقط، مرتبة من الأرخص، كلها تلقائية.
        followers_products = await _products_in(session, by_kind["followers"].id)
        likes_products = await _products_in(session, by_kind["likes"].id)
        assert len(followers_products) == 10
        assert len(likes_products) == 2
        prices = [product.price_usd for product in followers_products]
        assert prices == sorted(prices)
        # سعر = التكلفة + 50% (هامش) مقرباً لـ 3 خانات.
        cheapest_cost = Decimal("0.50")
        assert followers_products[0].cost_price_usd == cheapest_cost
        assert followers_products[0].price_usd == (cheapest_cost * Decimal("1.5")).quantize(
            Decimal("0.001")
        )
        assert all(product.is_auto_published for product in followers_products)
        assert all(product.status == ProductStatus.ACTIVE for product in followers_products)
        # لم يُنشر الأغلى (1.00 و1.10) خارج أرخص 10.
        refs = {p.provider_service_ref_id for p in followers_products}
        assert followers[-1].id not in refs  # 1.10
        assert followers[5].id not in refs  # 1.00
        assert followers[2].id in refs  # الأرخص 0.50 موجود
        assert all(p.provider_service_id for p in likes_products)


async def test_builder_is_idempotent_and_prunes_auto_products():
    async with async_session_maker() as session:
        provider = await _add_provider(session)
        rates = ("1.0", "0.9", "0.8", "0.7", "0.6", "0.5", "1.1", "1.2", "1.3", "1.4")
        for index, rate in enumerate(rates):
            await _add_service(
                session,
                provider,
                name=f"TikTok Views {index}",
                category="TikTok Views",
                rate=rate,
                external_id=f"tt-v-{index}",
            )

        await SmmSectionsService.build(session)
        async with async_session_maker() as session2:
            first = await SmmSectionsService.build(session2)
            assert first["products_created"] == 0  # لا تكرار
            assert first["sections_created"] == 0
            assert first["products_deactivated"] == 0

            # أضف خدمة أرخص جديدة ثم أعد البناء: الأغلى من الخمسة يتعطل.
            provider2 = await _add_provider(session2, name="Second")
            cheap = await _add_service(
                session2,
                provider2,
                name="TikTok Views UltraCheap",
                category="TikTok Views",
                rate="0.10",
                external_id="tt-v-cheap",
            )
            report = await SmmSectionsService.build(session2)
            assert report["products_created"] == 1

            smm_cat = await _smm_category(session2)
            tiktok = None
            for app in await DynamicService.get_active_root_sub_categories(session2, smm_cat.id):
                if app.name_ar == "تيك توك":
                    tiktok = app
            assert tiktok is not None
            sections = {
                s.kind_key: s
                for s in await DynamicService.get_active_child_sections(session2, tiktok.id)
            }
            products = await _products_in(session2, sections["views"].id)
            active = [p for p in products if p.status == ProductStatus.ACTIVE]
            assert len(active) == 10
            assert cheap.id in {p.provider_service_ref_id for p in active}
            # الأغلى (1.4) خرج من أرخص 10 وعُطّل تلقائياً (بقي في القاعدة).
            deactivated = [p for p in products if p.status == ProductStatus.INACTIVE]
            assert len(deactivated) >= 1
            assert all(p.is_auto_published for p in deactivated)


async def test_builder_keeps_manual_products_and_skips_published():
    async with async_session_maker() as session:
        provider = await _add_provider(session)
        service = await _add_service(
            session,
            provider,
            name="Instagram Comments 1",
            category="Instagram Comments",
            rate="0.40",
            external_id="ig-c-1",
        )

        # نشرة يدوية سابقة لنفس الخدمة — يجب ألا تتكرر ولا تُمس.
        smm_cat = await _smm_category(session)
        instagram = None
        for app in await DynamicService.get_active_root_sub_categories(session, smm_cat.id):
            if app.name_ar == "إنستغرام":
                instagram = app
        manual_section = SubCategory(
            category_id=smm_cat.id,
            parent_sub_category_id=instagram.id,
            kind_key="comments",
            name_ar="تعليقات",
            emoji="💬",
            is_active=True,
            sort_order=40,
        )
        session.add(manual_section)
        await session.commit()
        await session.refresh(manual_section)
        manual_product = await DynamicService.create_product(
            session,
            sub_category_id=manual_section.id,
            name_ar="تعليقات انستغرام يدوي",
            price_usd=Decimal("1.00"),
            cost_price_usd=Decimal("0.40"),
            api_provider_id=provider.id,
            provider_service_ref_id=service.id,
            provider_service_id=service.external_service_id,
            requires_link=True,
            requires_quantity=True,
            display_type=None,
            is_auto_published=False,
        )
        assert manual_product.is_auto_published is False

        report = await SmmSectionsService.build(session)
        # القسم اليدوي تبنّاه البناء بدل إنشاء نسخة ثانية.
        rebuilt_sections = await DynamicService.get_active_child_sections(session, instagram.id)
        comment_sections = [
            s for s in rebuilt_sections if s.kind_key == "comments"
        ]
        assert len(comment_sections) == 1
        products = await _products_in(session, comment_sections[0].id)
        assert len(products) == 1
        assert products[0].id == manual_product.id
        assert products[0].status == ProductStatus.ACTIVE
        assert products[0].is_auto_published is False
        assert report["products_created"] == 0


async def test_root_and_leaf_queries():
    async with async_session_maker() as session:
        smm_cat = await _smm_category(session)
        roots = await DynamicService.get_all_root_sub_categories(session, smm_cat.id)
        assert len(roots) == 10  # التطبيقات العشرة
        leaves = await DynamicService.get_active_leaf_sub_categories(session)
        # بدون أقسام داخلية كل التطبيقات «ورقات» ما زالت قابلة للنشر.
        assert any(leaf.category_id == smm_cat.id for leaf in leaves)
        # كل الأوراق بلا أبناء.
        parent_ids = {leaf.id for leaf in leaves}
        assert all(
            s.parent_sub_category_id not in parent_ids
            for s in await DynamicService.get_active_sub_categories(session, smm_cat.id)
        )


async def test_builder_adopts_manual_sibling_without_kind_key():
    """قسم يدوي بلا kind_key وبالاسم المعياري للنوع يُتبنّى لا يُكرَّر."""
    async with async_session_maker() as session:
        smm_cat = await _smm_category(session)
        instagram = None
        for app in await DynamicService.get_active_root_sub_categories(session, smm_cat.id):
            if app.name_ar == "إنستغرام":
                instagram = app
        manual = SubCategory(
            category_id=smm_cat.id,
            parent_sub_category_id=instagram.id,
            kind_key=None,  # قسم يدوي قديم بدون نوع
            name_ar="لايكات",
            emoji="❤️",
            is_active=True,
            sort_order=5,
        )
        session.add(manual)
        await session.commit()

        provider = await _add_provider(session)
        await _add_service(
            session,
            provider,
            name="Instagram Likes 1",
            category="Instagram Likes",
            rate="0.20",
            external_id="ig-l-manual-1",
        )

        report = await SmmSectionsService.build(session)
        assert report["sections_created"] == 0  # تبنٍّ لا إنشاء

        likes_sections = [
            s
            for s in await DynamicService.get_active_child_sections(session, instagram.id)
            if s.kind_key == "likes"
        ]
        assert len(likes_sections) == 1  # لا نسخة مكررة
        assert likes_sections[0].id == manual.id
        products = await _products_in(session, likes_sections[0].id)
        assert len(products) == 1  # المنشور وُضع في القسم اليدوي المتبنّى


# ═══════════════ حماية الإحالة ═══════════════


async def test_referral_guard_requires_check_and_completes():
    async with async_session_maker() as session:
        joiner = User(
            telegram_id=9911,
            username="joiner",
            full_name="Joiner",
            language_code="ar",
            referrer_id=None,
            referral_check_pending=True,
        )
        session.add(joiner)
        await session.commit()
        await session.refresh(joiner)

        assert joiner.referral_check_pending is True
        assert await ReferralGuardService.requires_check(joiner) is True

        await ReferralGuardService.complete(session, joiner)
        assert joiner.referral_check_pending is False
        assert joiner.referral_check_fails == 0


async def test_referral_guard_penalty_bans_joiner_and_referrer():
    async with async_session_maker() as session:
        referrer = User(telegram_id=5555, username="ref", full_name="Referrer")
        joiner = User(
            telegram_id=9912,
            username="botty",
            full_name="Bot?",
            language_code="ar",
            referrer_id=None,
            referral_check_pending=True,
            referral_check_fails=3,
        )
        session.add_all([referrer, joiner])
        await session.commit()
        await session.refresh(referrer)
        await session.refresh(joiner)
        joiner.referrer_id = referrer.id
        await session.commit()

        outcome = await ReferralGuardService.apply_penalty(session, joiner, bot=None)
        assert outcome["joiner_banned"] is True
        assert outcome["referrer_banned"] is True
        await session.refresh(joiner)
        await session.refresh(referrer)
        assert joiner.is_banned is True
        assert referrer.is_banned is True
        assert joiner.referrer_id is None
        assert joiner.referral_bonus_paid is True  # لا مكافأة مستقبلية
        events = list(
            (
                await session.execute(
                    select(AbuseEvent).where(
                        AbuseEvent.event_type == "referral_bot_suspected"
                    )
                )
            ).scalars().all()
        )
        assert events and events[0].user_id == joiner.id


# ═══════════════ الخدمات بلا سعر + حد أرخص 10 ═══════════════


async def test_default_limit_is_ten():
    """الافتراضي الجديد: أرخص 10 خدمات في كل قسم داخلي (بدل 5)."""
    from services.smm_sections_service import DEFAULT_MAX_PER_SECTION

    assert DEFAULT_MAX_PER_SECTION == 10
    assert await SmmSectionsService.limit() == 10


async def test_unpriced_services_are_never_published():
    """خدمات بسعر 0$ («سيرفر 1»، عناوين أقسام) لا تُنشر أبداً."""
    async with async_session_maker() as session:
        provider = await _add_provider(session)
        await _add_service(
            session,
            provider,
            name="Instagram Followers Server 1",
            category="Instagram Followers",
            rate="0",
            external_id="ig-srv-1",
        )
        await _add_service(
            session,
            provider,
            name="سيرفر 2 انستغرام متابعين",
            category="Instagram Followers",
            rate="0.00",
            external_id="ig-srv-2",
        )
        real = await _add_service(
            session,
            provider,
            name="Instagram Followers Real",
            category="Instagram Followers",
            rate="0.45",
            external_id="ig-real",
        )

        report = await SmmSectionsService.build(session)
        assert report["skipped_unpriced"] >= 2

        smm_cat = await _smm_category(session)
        instagram = None
        for app in await DynamicService.get_active_root_sub_categories(session, smm_cat.id):
            if app.name_ar == "إنستغرام":
                instagram = app
        sections = {
            s.kind_key: s
            for s in await DynamicService.get_active_child_sections(session, instagram.id)
        }
        products = await _products_in(session, sections["followers"].id)

    assert [p.provider_service_ref_id for p in products] == [real.id]
    assert all(p.price_usd > 0 for p in products)


async def test_is_sellable_service_filter():
    from services.smm_sections_service import is_sellable_service

    class _S:
        def __init__(self, rate):
            self.rate_usd = rate

    assert is_sellable_service(_S(Decimal("0.01"))) is True
    assert is_sellable_service(_S(Decimal("0"))) is False
    assert is_sellable_service(_S(None)) is False
    assert is_sellable_service(_S("-1")) is False


async def test_legacy_limit_upgrade_runs_once():
    """قاعدة قديمة محفوظ فيها 5 تُرفَع مرة واحدة إلى 10 ثم لا تُلمس."""
    from database.models import FeatureFlag
    from services.feature_service import FeatureService
    from services.settings_service import SettingsService
    from services.smm_sections_service import FEATURE_KEY

    async with async_session_maker() as session:
        await FeatureService.set_option(session, FEATURE_KEY, "max_per_section", 5)
    assert await SmmSectionsService.limit() == 5

    assert await SmmSectionsService.upgrade_legacy_limit() is True
    assert await SmmSectionsService.limit() == 10

    # الأدمن اختار قيمة خاصة بعد الترقية → لا تُلمس مجدداً.
    async with async_session_maker() as session:
        await FeatureService.set_option(session, FEATURE_KEY, "max_per_section", 3)
    assert await SmmSectionsService.upgrade_legacy_limit() is False
    assert await SmmSectionsService.limit() == 3

    async with async_session_maker() as session:
        flag = await session.get(FeatureFlag, FEATURE_KEY)
        assert flag is not None
    assert await SettingsService.get_bool(
        "smm_auto_sections_limit_upgraded_to_10", False
    )
