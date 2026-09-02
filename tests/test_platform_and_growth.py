"""
اختبارات رسمية للموجة الثانية من الخدمات: المقتنيات، امتدادات السوق،
طبقة الذكاء، طبقة المنصة، والنمو والقنوات.

هذه الاختبارات تغطي المسارات المالية والحساسة: من يستطيع الدفع، متى
تُفرج الأموال، وهل يمكن صرف مكافأة أو بيع أصل مرتين.
"""

from __future__ import annotations

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
    EscrowStatus,
    NumberOrder,
    OrderStatus,
    Product,
    ProductDisplayType,
    ProductFulfillmentType,
    ProductPricingType,
    ProductStatus,
    ProviderBid,
    ProviderName,
    ProviderService,
    ProviderServiceStatus,
    RevenueShareToken,
    SubCategory,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from services.collectibles_service import (
    ExchangeError,
    NumberExchangeService,
    NumberPortabilityService,
    RareDropService,
    VipCertificateService,
)
from services.feature_service import FeatureService
from services.growth_channels_service import (
    GrowthError,
    GrowthOptimizerService,
    MarketIntelligenceService,
    OmnichannelService,
    PooledRoomService,
    PublicStorefrontService,
    RevenueShareService,
    TaskToCreditService,
    VoiceOrderingService,
)
from services.marketplace_ext_service import (
    CrossCategoryCartService,
    EscrowError,
    EscrowService,
    P2PCodeMarketService,
)
from services.platform_service import (
    ComplianceService,
    DeveloperPlatformService,
    GamePriceTrackerService,
    ProviderBiddingService,
    PublicTrustService,
    WhiteLabelService,
)


# ═══════════════════════════ أدوات مساعدة ═══════════════════════════


async def _enable(*keys: str) -> None:
    async with async_session_maker() as session:
        for key in keys:
            await FeatureService.set_enabled(session, key, True)


async def _disable(*keys: str) -> None:
    async with async_session_maker() as session:
        for key in keys:
            await FeatureService.set_enabled(session, key, False)


async def _user(user_id: int, balance: str = "100", **kwargs) -> None:
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
                joined_at=kwargs.pop("joined_at", datetime.utcnow() - timedelta(days=5)),
                **kwargs,
            )
        )
        await session.commit()


async def _balance(user_id: int) -> Decimal:
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        return Decimal(str(user.balance))


async def _catalog() -> None:
    """قسمان ومنتجان في قسمين مختلفين، لاختبارات السلة العابرة."""
    async with async_session_maker() as session:
        if await session.get(Product, 1) is not None:
            return
        games = Category(name_ar="ألعاب", type=CategoryType.GAMES, is_active=True)
        smm = Category(name_ar="رشق", type=CategoryType.SMM, is_active=True)
        session.add_all([games, smm])
        await session.flush()
        sub_games = SubCategory(category_id=games.id, name_ar="ببجي", is_active=True)
        sub_smm = SubCategory(category_id=smm.id, name_ar="تيك توك", is_active=True)
        session.add_all([sub_games, sub_smm])
        await session.flush()
        session.add_all(
            [
                Product(
                    id=1, sub_category_id=sub_games.id, name_ar="شحن ببجي 60 UC",
                    price_usd=Decimal("1.00"), cost_price_usd=Decimal("0.5"),
                    pricing_type=ProductPricingType.FIXED,
                    display_type=ProductDisplayType.FIXED_TOTAL,
                    status=ProductStatus.ACTIVE, total_sold=42,
                    description="شحن فوري", min_quantity=1, max_quantity=1,
                ),
                Product(
                    id=2, sub_category_id=sub_smm.id, name_ar="1000 متابع تيك توك",
                    price_usd=Decimal("2.00"), cost_price_usd=Decimal("1.0"),
                    pricing_type=ProductPricingType.FIXED,
                    display_type=ProductDisplayType.PER_1000,
                    status=ProductStatus.ACTIVE, total_sold=10,
                    min_quantity=100, max_quantity=100000,
                ),
            ]
        )
        await session.commit()


# ═══════════════════════════ بورصة الأرقام ═══════════════════════════


@pytest.mark.asyncio
async def test_limit_order_fires_once_at_target_price():
    await _enable("number_exchange")
    await _user(2101)

    async with async_session_maker() as session:
        order = await NumberExchangeService.place_limit_order(
            session, 2101, "tg", "tr", 50, Decimal("0.28")
        )
        order_id = order["order_id"]

    # سعر أعلى من المستهدف لا ينفذ الأمر
    async with async_session_maker() as session:
        assert await NumberExchangeService.match(session, "tg", "tr", Decimal("0.35")) == []

    # سعر أقل ينفذه
    async with async_session_maker() as session:
        matched = await NumberExchangeService.match(session, "tg", "tr", Decimal("0.25"))
    assert len(matched) == 1
    assert matched[0]["quantity"] == 50

    # ولا ينفذه مرتين
    async with async_session_maker() as session:
        assert await NumberExchangeService.match(session, "tg", "tr", Decimal("0.20")) == []


@pytest.mark.asyncio
async def test_disabled_exchange_places_no_orders():
    await _disable("number_exchange")
    await _user(2102)
    async with async_session_maker() as session:
        with pytest.raises(ExchangeError):
            await NumberExchangeService.place_limit_order(
                session, 2102, "tg", "tr", 10, Decimal("0.10")
            )


# ═══════════════════════════ قابلية النقل ═══════════════════════════


@pytest.mark.asyncio
async def test_portability_is_owner_scoped():
    await _enable("number_portability")
    await _user(2201)
    await _user(2202)
    async with async_session_maker() as session:
        session.add(
            NumberOrder(
                id=2201, user_id=2201, provider=ProviderName.FIVESIM,
                provider_order_id="p1", service="tg", country_code="tr",
                phone_number="+905557000001",
                price_provider_usd=Decimal("0.2"), price_sell_usd=Decimal("0.5"),
                status=OrderStatus.COMPLETED,
                purchased_at=datetime.utcnow() - timedelta(days=1),
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        reserved = await NumberPortabilityService.reserve(session, 2201, 2201)
        assert reserved is not None
        # مستخدم آخر لا يستطيع حجزه
        assert await NumberPortabilityService.reserve(session, 2201, 2202) is None
        reclaimable = await NumberPortabilityService.reclaimable(session, 2201)
    assert len(reclaimable) == 1


# ═══════════════════════════ الأرقام النادرة ═══════════════════════════


@pytest.mark.asyncio
async def test_rare_pattern_detection_is_not_over_eager():
    """
    regression: النمط الساذج (\\d+)\\1+ كان يطابق أي رقم مكرر مثل "55"،
    فيصير كل رقم تقريباً نادراً ويفقد الوسم معناه.
    """
    await _enable("rare_number_drops")
    assert await RareDropService.is_rare("+905551111111") is True  # سبعة 1
    assert await RareDropService.is_rare("+905551212121") is True  # نمط متناوب
    # رقم عادي فيه "55" مكررة يجب ألا يُعتبر نادراً
    assert await RareDropService.is_rare("+905551234567") is False


@pytest.mark.asyncio
async def test_vip_certificate_cannot_be_double_issued_and_transfers():
    await _enable("vip_number_certificates", "rare_number_drops")
    await _user(2301, "100")
    await _user(2302, "0")

    async with async_session_maker() as session:
        cert = await VipCertificateService.issue(session, 2301, "+905559999999")
        with pytest.raises(ExchangeError):
            await VipCertificateService.issue(session, 2301, "+905559999999")
        # رقم غير نادر لا يستحق شهادة
        with pytest.raises(ExchangeError):
            await VipCertificateService.issue(session, 2301, "+905551234567")

        assert await VipCertificateService.transfer(session, cert["serial"], 2301, 2302) is True
        assert await VipCertificateService.owned_by(session, 2301) == []
        assert len(await VipCertificateService.owned_by(session, 2302)) == 1
        # المالك القديم لم يعد يستطيع تحويلها
        assert await VipCertificateService.transfer(session, cert["serial"], 2301, 2302) is False


# ═══════════════════════════ Escrow مستقل ═══════════════════════════


@pytest.mark.asyncio
async def test_escrow_release_deducts_commission_and_is_one_shot():
    await _enable("escrow_engine")
    await _user(2401, "50")
    await _user(2402, "100")

    async with async_session_maker() as session:
        hold = await EscrowService.hold(session, 2402, Decimal("20"), "اختبار", "ref")
    assert await _balance(2402) == Decimal("80")

    async with async_session_maker() as session:
        result = await EscrowService.release(session, hold["hold_id"], 2401, Decimal("5"))
    assert result["commission_usd"] == Decimal("1.0000")
    assert result["released_usd"] == Decimal("19.0000")
    assert await _balance(2401) == Decimal("69.0000")

    # لا إفراج مزدوج
    async with async_session_maker() as session:
        with pytest.raises(EscrowError):
            await EscrowService.release(session, hold["hold_id"], 2401)
    assert await _balance(2401) == Decimal("69.0000")


@pytest.mark.asyncio
async def test_escrow_refund_returns_full_amount():
    await _enable("escrow_engine")
    await _user(2411, "50")

    async with async_session_maker() as session:
        hold = await EscrowService.hold(session, 2411, Decimal("10"), "اختبار", "ref2")
    assert await _balance(2411) == Decimal("40")

    async with async_session_maker() as session:
        assert await EscrowService.refund(session, hold["hold_id"], "ألغيت") is True
    assert await _balance(2411) == Decimal("50")


# ═══════════════════════════ سوق الأكواد P2P ═══════════════════════════


@pytest.mark.asyncio
async def test_p2p_code_is_encrypted_and_sells_once():
    await _enable("p2p_code_market")
    await _user(2501, "50")
    await _user(2502, "100")

    async with async_session_maker() as session:
        listing = await P2PCodeMarketService.list_code(
            session, 2501, "بطاقة ستيم", Decimal("10"), "STEAM-SECRET-42"
        )
        from database.models import P2PCodeListing

        stored = await session.get(P2PCodeListing, listing["listing_id"])
        # الكود مخزن مشفراً لا نصاً صريحاً
        assert stored.encrypted_code
        assert "STEAM-SECRET-42" not in stored.encrypted_code

        result = await P2PCodeMarketService.buy(session, listing["listing_id"], 2502)

    assert result["code"] == "STEAM-SECRET-42"
    assert result["commission_usd"] == Decimal("0.4000")

    # لا يُباع مرتين
    async with async_session_maker() as session:
        with pytest.raises(EscrowError):
            await P2PCodeMarketService.buy(session, listing["listing_id"], 2502)

    # كود فارغ مرفوض
    async with async_session_maker() as session:
        with pytest.raises(EscrowError):
            await P2PCodeMarketService.list_code(session, 2501, "x", Decimal("5"), "  ")


# ═══════════════════════════ سلة عابرة للأقسام ═══════════════════════════


@pytest.mark.asyncio
async def test_bundle_discount_only_applies_across_categories():
    await _enable("cross_category_cart")
    await _catalog()

    async with async_session_maker() as session:
        multi = await CrossCategoryCartService.quote(session, [1, 2], [1, 1000])
        single = await CrossCategoryCartService.quote(session, [1], [1])

    assert multi["categories"] == 2
    assert multi["discount_usd"] > 0
    assert single["categories"] == 1
    # الخصم ليس مجانياً: قسم واحد لا خصم عليه
    assert single["discount_usd"] == Decimal("0")


# ═══════════════════════════ الامتثال ═══════════════════════════


@pytest.mark.asyncio
async def test_jurisdiction_rule_blocks_service_in_country():
    await _enable("compliance_engine")
    async with async_session_maker() as session:
        from database.models import JurisdictionRule

        session.add(
            JurisdictionRule(
                country_code="DE", blocked_services="tg,wa", reason="غير متاح في ألمانيا"
            )
        )
        await session.commit()

        allowed, reason = await ComplianceService.is_allowed(session, "DE", "tg")
        assert allowed is False
        assert reason == "غير متاح في ألمانيا"

        allowed, _ = await ComplianceService.is_allowed(session, "TR", "tg")
        assert allowed is True


# ═══════════════════════════ توقيع الويبhook ═══════════════════════════


def test_webhook_signature_verifies_and_rejects_tampering():
    secret = "topsecret"
    payload = b'{"event":"order.completed"}'
    signature = DeveloperPlatformService.sign(secret, payload)

    assert DeveloperPlatformService.verify(secret, payload, signature) is True
    assert DeveloperPlatformService.verify(secret, payload, signature[:-1] + "0") is False
    assert DeveloperPlatformService.verify("wrong-secret", payload, signature) is False
    assert DeveloperPlatformService.verify(secret, payload, "") is False


def test_webhook_scopes_are_enforced():
    assert DeveloperPlatformService.has_scope("read:catalog,write:orders", "write:orders")
    assert not DeveloperPlatformService.has_scope("read:catalog", "write:orders")
    assert DeveloperPlatformService.has_scope("*", "anything")


# ═══════════════════════════ مزايدة المزودين ═══════════════════════════


@pytest.mark.asyncio
async def test_cheapest_live_bid_wins_and_expired_is_excluded():
    await _enable("provider_bidding")
    async with async_session_maker() as session:
        if await session.get(ApiProvider, 1) is None:
            session.add(
                ApiProvider(
                    id=1, name="P1", type=ApiProviderType.SMM,
                    protocol_type=ApiProtocolType.SMM_V2, api_url="http://a",
                    api_key="k", is_active=True,
                )
            )
            session.add(
                ApiProvider(
                    id=2, name="P2", type=ApiProviderType.SMM,
                    protocol_type=ApiProtocolType.SMM_V2, api_url="http://b",
                    api_key="k", is_active=True,
                )
            )
            await session.commit()

        await ProviderBiddingService.submit_bid(session, 1, "tg", "tr", Decimal("0.30"), 15)
        await ProviderBiddingService.submit_bid(session, 2, "tg", "tr", Decimal("0.22"), 15)
        best = await ProviderBiddingService.best_bid(session, "tg", "tr")
    assert best["price_usd"] == Decimal("0.22")
    assert best["provider_id"] == 2

    # عرض منتهي فعلياً لا يُعتمد
    async with async_session_maker() as session:
        await ProviderBiddingService.submit_bid(session, 1, "wa", "tr", Decimal("0.10"), 15)
        bid = (
            await session.execute(
                select(ProviderBid).where(
                    ProviderBid.service_code == "wa", ProviderBid.country_code == "tr"
                )
            )
        ).scalars().first()
        bid.expires_at = datetime.utcnow() - timedelta(minutes=1)
        await session.commit()

        assert await ProviderBiddingService.best_bid(session, "wa", "tr") is None
        assert await ProviderBiddingService.cleanup_expired(session) >= 1


# ═══════════════════════════ العلامة البيضاء ═══════════════════════════


@pytest.mark.asyncio
async def test_tenant_resolution_and_duplicate_slug_rejected():
    await _enable("white_label_factory")
    async with async_session_maker() as session:
        created = await WhiteLabelService.create(session, "myshop", "متجري", "@myshop_bot", Decimal("7"))
        resolved = await WhiteLabelService.resolve(session, "myshop_bot")
    assert resolved["tenant"] == "myshop"
    assert resolved["brand"] == "متجري"

    async with async_session_maker() as session:
        fallback = await WhiteLabelService.resolve(session, "unknown_bot")
    assert fallback["tenant"] == "default"

    async with async_session_maker() as session:
        from services.platform_service import PlatformError

        with pytest.raises(PlatformError):
            await WhiteLabelService.create(session, "myshop", "ثاني", "@other", Decimal("5"))


# ═══════════════════════════ أسهم حصة الإحالة ═══════════════════════════


@pytest.mark.asyncio
async def test_revenue_share_issue_pays_seller_and_buy_is_one_shot():
    await _enable("revenue_sharing_tokens", "affiliate_tiers")
    await _user(2601, "0")
    await _user(2602, "50")
    await _user(2603, "0", referrer_id=2601, total_spent_usd=Decimal("200"))

    async with async_session_maker() as session:
        token = await RevenueShareService.issue(session, 2601, 20, Decimal("15"))
    assert await _balance(2601) == Decimal("15")

    async with async_session_maker() as session:
        assert await RevenueShareService.buy(session, token["token_id"], 2602) is True
    assert await _balance(2602) == Decimal("35")

    # لا يُشترى مرتين
    async with async_session_maker() as session:
        assert await RevenueShareService.buy(session, token["token_id"], 2603) is False

    # التوزيع يدفع النسبة الصحيحة
    async with async_session_maker() as session:
        paid = await RevenueShareService.distribute(session, 2601, Decimal("10"))
    assert paid and paid[0]["share_usd"] == Decimal("2.0000")


@pytest.mark.asyncio
async def test_revenue_share_rejects_excessive_share():
    await _enable("revenue_sharing_tokens")
    await _user(2611, "0")
    async with async_session_maker() as session:
        with pytest.raises(GrowthError):
            await RevenueShareService.issue(session, 2611, 99, Decimal("10"))


# ═══════════════════════════ مهام مقابل رصيد ═══════════════════════════


@pytest.mark.asyncio
async def test_task_to_credit_pays_once_per_proof():
    await _enable("task_to_credit")
    await _user(2701, "0")

    async with async_session_maker() as session:
        reward = await TaskToCreditService.complete(session, 2701, "translate_text", "proof-1")
    assert reward["reward_usd"] > 0
    assert await _balance(2701) == reward["reward_usd"]

    # نفس الدليل لا يُصرف مرتين
    async with async_session_maker() as session:
        with pytest.raises(GrowthError):
            await TaskToCreditService.complete(session, 2701, "translate_text", "proof-1")

    # دليل مختلف يُقبل
    async with async_session_maker() as session:
        await TaskToCreditService.complete(session, 2701, "translate_text", "proof-2")
    assert await _balance(2701) == reward["reward_usd"] * 2


# ═══════════════════════════ غرف الشراء الجماعي ═══════════════════════════


@pytest.mark.asyncio
async def test_pooled_room_quorum_tracking():
    await _enable("pooled_rooms")
    await _catalog()
    await _user(2801, "0")
    await _user(2802, "0")
    await _user(2803, "0")

    async with async_session_maker() as session:
        room = await PooledRoomService.create(session, 2801, 1, 5000)
        first = await PooledRoomService.join(session, room["room_id"], 2802)
    assert first["members"] == 2
    assert first["ready"] is False

    async with async_session_maker() as session:
        second = await PooledRoomService.join(session, room["room_id"], 2803)
    assert second["members"] == 3
    # النصاب الافتراضي 5، فثلاثة أعضاء لا تكفي
    assert second["ready"] is False


# ═══════════════════════════ محرك النمو ═══════════════════════════


@pytest.mark.asyncio
async def test_ab_winner_requires_minimum_samples_and_counts_correctly():
    """
    regression: SUM على عمود Boolean كان يحوَّل إلى True/False، فيصير
    كل متغير درجته 1 مهما كانت عيناته. الآن يُجمع كعدد صحيح.
    """
    await _enable("growth_optimizer")

    async with async_session_maker() as session:
        # عيّنات قليلة: لا قرار
        for index in range(5):
            await GrowthOptimizerService.record_variant(session, "small", "A", index % 2 == 0)
            await GrowthOptimizerService.record_variant(session, "small", "B", True)
        assert await GrowthOptimizerService.winner(session, "small", min_samples=30) is None

        # عيّنات كافية: A تحويلها 25% وB تحويلها ~33%
        for index in range(40):
            await GrowthOptimizerService.record_variant(session, "price", "A", index % 4 == 0)
            await GrowthOptimizerService.record_variant(session, "price", "B", index % 3 == 0)
        result = await GrowthOptimizerService.winner(session, "price", min_samples=30)

    assert result is not None
    assert result["winner"]["variant"] == "B"
    assert result["winner"]["conversion_rate"] > 30


# ═══════════════════════════ المتجر العام ═══════════════════════════


@pytest.mark.asyncio
async def test_public_storefront_exposes_no_internal_cost_data():
    await _enable("public_storefront")
    await _catalog()

    async with async_session_maker() as session:
        page = await PublicStorefrontService.page_for(session, 1)
        sitemap = await PublicStorefrontService.sitemap(session)

    assert page["price_usd"] == Decimal("1.0000")
    assert page["slug"]
    # لا سعر تكلفة ولا بيانات داخلية
    assert "cost_price_usd" not in page
    assert "cost_price" not in page
    assert sitemap


# ═══════════════════════════ تعدد القنوات ═══════════════════════════


@pytest.mark.asyncio
async def test_inbound_messages_normalise_across_channels():
    await _enable("omnichannel")
    cases = [
        ("telegram", {"from_id": 9, "text": "hello"}),
        ("whatsapp", {"wa_id": "961", "message": {"body": "hello"}}),
        ("discord", {"author": {"id": "d1"}, "content": "hello"}),
    ]
    for channel, payload in cases:
        normalised = await OmnichannelService.normalize_inbound(channel, payload)
        assert normalised["channel"] == channel
        assert normalised["text"] == "hello"
        assert normalised["user_ref"]


# ═══════════════════════════ الطلب الصوتي ═══════════════════════════


def test_voice_intent_parsing():
    assert VoiceOrderingService.parse_intent("بدي أشحن رصيد")["intent"] == "deposit"
    assert VoiceOrderingService.parse_intent("أعطيني رقم تليجرام")["intent"] == "buy_number"
    assert VoiceOrderingService.parse_intent("بدي متابعين")["intent"] == "buy_smm"
    assert VoiceOrderingService.parse_intent("كم رصيدي")["intent"] == "check_balance"
    assert VoiceOrderingService.parse_intent("كلام غير مفهوم")["intent"] == "unknown"


@pytest.mark.asyncio
async def test_voice_transcribe_degrades_cleanly_without_provider():
    await _enable("voice_ordering")
    text, ok = await VoiceOrderingService.transcribe("/nonexistent/file.ogg")
    assert ok is False
    assert text is None


# ═══════════════════════════ ذكاء السوق ═══════════════════════════


@pytest.mark.asyncio
async def test_market_intelligence_is_anonymised():
    await _enable("market_intelligence")
    await _user(2901, "0")
    async with async_session_maker() as session:
        session.add(
            NumberOrder(
                id=2901, user_id=2901, provider=ProviderName.FIVESIM,
                provider_order_id="mi1", service="tg", country_code="tr",
                phone_number="+905558880001",
                price_provider_usd=Decimal("0.2"), price_sell_usd=Decimal("0.5"),
                status=OrderStatus.COMPLETED, purchased_at=datetime.utcnow(),
            )
        )
        await session.commit()

        report = await MarketIntelligenceService.report(session, 30)

    assert report["anonymized"] is True
    assert report["total_volume_usd"] == Decimal("0.5")
    # لا أسماء مستخدمين ولا أرقام في التقرير
    assert report["by_country"] and report["by_country"][0]["country"] == "tr"
    dumped = str(report)
    assert "user2901" not in dumped
    assert "+905558880001" not in dumped


@pytest.mark.asyncio
async def test_public_trust_check_does_not_leak_owner():
    await _enable("public_trust_api")
    async with async_session_maker() as session:
        unknown = await PublicTrustService.check_number(session, "+905550000000")
    assert unknown["known"] is False
    assert "username" not in unknown
    assert "user_id" not in unknown


# ═══════════════════════════ تتبع أسعار الألعاب ═══════════════════════════


@pytest.mark.asyncio
async def test_game_price_comparison_sorted_cheapest_first():
    await _enable("game_price_tracker")
    async with async_session_maker() as session:
        if await session.get(ApiProvider, 1) is None:
            session.add(
                ApiProvider(
                    id=1, name="P1", type=ApiProviderType.SMM,
                    protocol_type=ApiProtocolType.SMM_V2, api_url="http://a",
                    api_key="k", is_active=True,
                )
            )
            session.add(
                ApiProvider(
                    id=2, name="P2", type=ApiProviderType.SMM,
                    protocol_type=ApiProtocolType.SMM_V2, api_url="http://b",
                    api_key="k", is_active=True,
                )
            )
            await session.flush()
            session.add_all(
                [
                    ProviderService(
                        id=101, api_provider_id=1, external_service_id="ff1",
                        name="جواهر فري فاير", rate=Decimal("0.50"),
                        rate_usd=Decimal("0.50"), status=ProviderServiceStatus.ACTIVE,
                    ),
                    ProviderService(
                        id=102, api_provider_id=2, external_service_id="ff2",
                        name="جواهر فري فاير", rate=Decimal("0.35"),
                        rate_usd=Decimal("0.35"), status=ProviderServiceStatus.ACTIVE,
                    ),
                ]
            )
            await session.commit()

        rows = await GamePriceTrackerService.compare(session, "فري فاير")
        cheapest = await GamePriceTrackerService.cheapest_provider(session, "فري فاير")

    assert rows[0]["rate_usd"] == Decimal("0.35")
    assert cheapest["provider_id"] == 2
