"""
اختبارات طبقة الإضافات الجديدة: أعلام الميزات، اقتصاد النقاط، المهام،
وسوق المستخدمين مع محرك الضمان.

هذه الاختبارات تغطي المسارات المالية الحساسة: من يستطيع الدفع، متى
تُفرج الأموال، وهل يمكن بيع الإعلان مرتين أو صرف المكافأة مرتين.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    EscrowStatus,
    MarketListing,
    MarketListingStatus,
    MarketTransaction,
    NumberOrder,
    OrderStatus,
    ProviderName,
    Task,
    TaskSubmission,
    TaskVerification,
    User,
)
from services.feature_registry import FEATURES
from services.feature_service import FeatureService
from services.marketplace_service import MarketplaceService, MarketError
from services.player_id_service import PlayerIdError, PlayerIdService
from services.points_service import PointsError, PointsService
from services.task_service import TaskError, TaskService


# ═══════════════════════════ أدوات مساعدة ═══════════════════════════


async def _make_user(
    user_id: int,
    *,
    balance: str = "0",
    points: int = 0,
    age_hours: int = 100,
) -> None:
    async with async_session_maker() as session:
        session.add(
            User(
                id=user_id,
                telegram_id=user_id * 1000,
                username=f"user{user_id}",
                full_name=f"U{user_id}",
                balance=Decimal(balance),
                loyalty_points=points,
                joined_at=datetime.utcnow() - timedelta(hours=age_hours),
            )
        )
        await session.commit()


async def _balance(user_id: int) -> Decimal:
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        return Decimal(str(user.balance))


async def _points(user_id: int) -> int:
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        return int(user.loyalty_points or 0)


# ═══════════════════════════ سجل الميزات ═══════════════════════════


@pytest.mark.asyncio
async def test_every_registered_feature_is_controllable_and_persisted():
    await FeatureService.sync_registry()
    await FeatureService.reload()

    snapshot = await FeatureService.snapshot()
    assert len(snapshot) == len(FEATURES)

    # مفاتيح فريدة ولا تكرار
    keys = [spec.key for spec in FEATURES]
    assert len(keys) == len(set(keys))

    # إيقاف ميزة ينعكس فوراً وبدون إعادة تشغيل
    async with async_session_maker() as session:
        assert await FeatureService.set_enabled(session, "bulk_numbers", True)
    assert await FeatureService.enabled("bulk_numbers") is True
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "bulk_numbers", False)
    assert await FeatureService.enabled("bulk_numbers") is False


@pytest.mark.asyncio
async def test_unknown_feature_is_disabled_and_option_types_are_protected():
    assert await FeatureService.enabled("does_not_exist") is False

    async with async_session_maker() as session:
        # قيمة خاطئة يجب ألا تكسر النوع المتوقع
        await FeatureService.set_option(session, "bulk_numbers", "max_quantity", "not-a-number")
        await FeatureService.set_option(session, "bulk_numbers", "max_quantity", "250")
    assert await FeatureService.config_int("bulk_numbers", "max_quantity") == 250

    async with async_session_maker() as session:
        await FeatureService.set_option(session, "instant_delivery", "enable_webhooks", "false")
    assert await FeatureService.config_bool("instant_delivery", "enable_webhooks") is False


# ═══════════════════════════ اقتصاد النقاط ═══════════════════════════


@pytest.mark.asyncio
async def test_points_rate_is_one_hundred_per_dollar_by_default():
    assert await PointsService.points_per_usd() == 100
    assert await PointsService.usd_for_points(450) == Decimal("4.5000")
    assert await PointsService.points_for_usd(Decimal("3.50")) == 350


@pytest.mark.asyncio
async def test_points_quote_splits_points_and_cash_correctly():
    await FeatureService.sync_registry()
    await _make_user(11, balance="10", points=450)
    async with async_session_maker() as session:
        quote = await PointsService.quote(session, 11, Decimal("10"))
    assert quote["points_used"] == 450
    assert quote["points_usd"] == Decimal("4.5000")
    assert quote["cash_usd"] == Decimal("5.5000")


@pytest.mark.asyncio
async def test_points_spend_deducts_points_and_refuses_when_insufficient():
    await FeatureService.sync_registry()
    await _make_user(12, balance="0", points=300)
    async with async_session_maker() as session:
        value = await PointsService.spend(session, 12, 200, "اختبار")
    assert value == Decimal("2.0000")
    assert await _points(12) == 100

    async with async_session_maker() as session:
        with pytest.raises(PointsError):
            await PointsService.spend(session, 12, 500, "أكثر مما يملك")
    assert await _points(12) == 100  # لم يتغير


@pytest.mark.asyncio
async def test_disabling_points_feature_blocks_spending():
    await FeatureService.sync_registry()
    await _make_user(13, points=500)
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "points_currency", False)
    async with async_session_maker() as session:
        with pytest.raises(PointsError):
            await PointsService.spend(session, 13, 100, "موقوف")
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "points_currency", True)


# ═══════════════════════════ المهام ═══════════════════════════


@pytest.mark.asyncio
async def test_daily_task_cannot_be_completed_twice_in_one_day():
    await FeatureService.sync_registry()
    await _make_user(21, points=0)
    async with async_session_maker() as session:
        await TaskService.seed_defaults(session)
        checkin = await TaskService.get_by_key(session, "daily_checkin")
        assert checkin is not None
        result = await TaskService.complete(session, 21, checkin)
        assert result["points"] == checkin.reward_points
    assert await _points(21) == checkin.reward_points

    async with async_session_maker() as session:
        checkin = await TaskService.get_by_key(session, "daily_checkin")
        with pytest.raises(TaskError):
            await TaskService.complete(session, 21, checkin)
    assert await _points(21) == checkin.reward_points  # لم تُصرف مرتين


@pytest.mark.asyncio
async def test_new_account_is_blocked_by_minimum_age_rule():
    await FeatureService.sync_registry()
    await _make_user(22, age_hours=0)
    async with async_session_maker() as session:
        await TaskService.seed_defaults(session)
        checkin = await TaskService.get_by_key(session, "daily_checkin")
        ok, reason = await TaskService.eligibility(session, 22, checkin)
    assert ok is False
    assert "ساعة" in reason


@pytest.mark.asyncio
async def test_admin_approval_task_pays_once_and_only_once():
    await FeatureService.sync_registry()
    await _make_user(23, points=0)
    async with async_session_maker() as session:
        await TaskService.seed_defaults(session)
        report = await TaskService.get_by_key(session, "report_provider")
        submission = await TaskService.submit(session, 23, report, "المزود بطيء")
        submission_id = submission.id

    before = await _points(23)
    async with async_session_maker() as session:
        await TaskService.review_submission(session, submission_id, admin_id=1, approve=True)
    after = await _points(23)
    assert after == before + 300

    # إعادة المراجعة يجب ألا تصرف شيئاً
    async with async_session_maker() as session:
        again = await TaskService.review_submission(session, submission_id, admin_id=1, approve=True)
    assert again is None
    assert await _points(23) == after


@pytest.mark.asyncio
async def test_disabling_tasks_system_blocks_completion():
    await FeatureService.sync_registry()
    await _make_user(24, points=0)
    async with async_session_maker() as session:
        await TaskService.seed_defaults(session)
        await FeatureService.set_enabled(session, "tasks_system", False)
        checkin = await TaskService.get_by_key(session, "daily_checkin")
        ok, reason = await TaskService.eligibility(session, 24, checkin)
    assert ok is False and "موقوف" in reason
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "tasks_system", True)


# ═══════════════════════════ سوق المستخدمين ═══════════════════════════


@pytest.mark.asyncio
async def test_digital_code_is_stored_encrypted_never_plaintext():
    await FeatureService.sync_registry()
    await _make_user(31, balance="20")
    async with async_session_maker() as session:
        listing = await MarketplaceService.create_listing(
            session,
            seller_id=31,
            kind="digital_code",
            title="بطاقة ستيم",
            description="جديدة",
            price_usd=Decimal("45"),
            secret_code="STEAM-SECRET-9999",
        )
        assert listing.status == MarketListingStatus.PENDING_REVIEW
        assert listing.secret_payload
        assert "STEAM-SECRET-9999" not in listing.secret_payload


@pytest.mark.asyncio
async def test_listing_requires_minimum_account_age_and_balance():
    await FeatureService.sync_registry()
    await _make_user(32, balance="20", age_hours=0)
    await _make_user(33, balance="0.10", age_hours=100)

    async with async_session_maker() as session:
        with pytest.raises(MarketError):
            await MarketplaceService.create_listing(
                session, 32, "service", "x", "y", Decimal("10")
            )
    async with async_session_maker() as session:
        with pytest.raises(MarketError):
            await MarketplaceService.create_listing(
                session, 33, "service", "x", "y", Decimal("10")
            )


@pytest.mark.asyncio
async def test_sms_number_listing_requires_proof_of_ownership():
    await FeatureService.sync_registry()
    await _make_user(34, balance="20")
    async with async_session_maker() as session:
        session.add(
            NumberOrder(
                id=555,
                user_id=34,
                provider=ProviderName.FIVESIM,
                provider_order_id="ext-1",
                service="tg",
                country_code="tr",
                phone_number="+905550001111",
                price_provider_usd=Decimal("0.2"),
                price_sell_usd=Decimal("0.5"),
                status=OrderStatus.COMPLETED,
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        with pytest.raises(MarketError):
            await MarketplaceService.create_listing(
                session, 34, "sms_number", "رقم", "x", Decimal("5"), ownership_proof="99999"
            )
    async with async_session_maker() as session:
        listing = await MarketplaceService.create_listing(
            session, 34, "sms_number", "رقم", "x", Decimal("5"), ownership_proof="555"
        )
        assert listing.status == MarketListingStatus.PENDING_REVIEW


@pytest.mark.asyncio
async def test_commission_is_added_on_top_of_seller_price():
    await FeatureService.sync_registry()
    await _make_user(35, balance="20")
    async with async_session_maker() as session:
        listing = await MarketplaceService.create_listing(
            session, 35, "service", "خدمة", "x", Decimal("45")
        )
        listing_id = listing.id
    async with async_session_maker() as session:
        approved = await MarketplaceService.approve(
            session, listing_id, admin_id=1, commission_percent=Decimal("10")
        )
        total = await MarketplaceService.total_price(approved)
        commission = await MarketplaceService.commission_amount(approved)
    assert commission == Decimal("4.5000")
    assert total == Decimal("49.5000")
    assert approved.status == MarketListingStatus.APPROVED


async def _approved_listing(seller_id: int, price: str = "45", commission: str = "10") -> int:
    async with async_session_maker() as session:
        listing = await MarketplaceService.create_listing(
            session, seller_id, "service", "خدمة", "x", Decimal(price)
        )
        listing_id = listing.id
    async with async_session_maker() as session:
        await MarketplaceService.approve(
            session, listing_id, admin_id=1, commission_percent=Decimal(commission)
        )
    return listing_id


@pytest.mark.asyncio
async def test_purchase_escrows_money_and_seller_receives_nothing_yet():
    await FeatureService.sync_registry()
    await _make_user(41, balance="20")
    await _make_user(42, balance="100")
    listing_id = await _approved_listing(41, "45", "10")

    async with async_session_maker() as session:
        tx = await MarketplaceService.purchase(session, listing_id, 42)
        assert tx.status == EscrowStatus.FUNDED
        assert tx.total_charged_usd == Decimal("49.5000")

    assert await _balance(42) == Decimal("50.5000")  # خُصم من المشتري
    assert await _balance(41) == Decimal("20")  # لم يصل البائع شيء


@pytest.mark.asyncio
async def test_listing_cannot_be_sold_twice():
    await FeatureService.sync_registry()
    await _make_user(43, balance="20")
    await _make_user(44, balance="500")
    listing_id = await _approved_listing(43, "10", "5")

    async with async_session_maker() as session:
        await MarketplaceService.purchase(session, listing_id, 44)
    async with async_session_maker() as session:
        with pytest.raises(MarketError):
            await MarketplaceService.purchase(session, listing_id, 44)


@pytest.mark.asyncio
async def test_failed_payment_returns_listing_to_market():
    await FeatureService.sync_registry()
    await _make_user(45, balance="20")
    await _make_user(46, balance="1")  # لا يكفي
    listing_id = await _approved_listing(45, "45", "10")

    async with async_session_maker() as session:
        with pytest.raises(MarketError):
            await MarketplaceService.purchase(session, listing_id, 46)
    async with async_session_maker() as session:
        listing = await session.get(MarketListing, listing_id)
        assert listing.status == MarketListingStatus.APPROVED


@pytest.mark.asyncio
async def test_release_pays_seller_price_only_and_keeps_commission():
    await FeatureService.sync_registry()
    await _make_user(51, balance="20")
    await _make_user(52, balance="100")
    listing_id = await _approved_listing(51, "45", "10")

    async with async_session_maker() as session:
        tx = await MarketplaceService.purchase(session, listing_id, 52)
        tx_id = tx.id
    async with async_session_maker() as session:
        await MarketplaceService.mark_delivered(session, tx_id, actor_id=51)
        assert await MarketplaceService.release(session, tx_id, admin_id=1) is True

    assert await _balance(51) == Decimal("65")  # 20 + 45 (ليس 49.5)
    async with async_session_maker() as session:
        stats = await MarketplaceService.stats(session)
    assert stats["commission_usd"] == Decimal("4.5000")


@pytest.mark.asyncio
async def test_release_is_idempotent_and_cannot_run_twice():
    await FeatureService.sync_registry()
    await _make_user(53, balance="20")
    await _make_user(54, balance="100")
    listing_id = await _approved_listing(53, "20", "10")
    async with async_session_maker() as session:
        tx = await MarketplaceService.purchase(session, listing_id, 54)
        tx_id = tx.id
    async with async_session_maker() as session:
        assert await MarketplaceService.release(session, tx_id, admin_id=1) is True
    async with async_session_maker() as session:
        assert await MarketplaceService.release(session, tx_id, admin_id=1) is False
    assert await _balance(53) == Decimal("40")  # مرة واحدة فقط


@pytest.mark.asyncio
async def test_dispute_freezes_release_until_admin_decides():
    await FeatureService.sync_registry()
    await _make_user(61, balance="20")
    await _make_user(62, balance="100")
    listing_id = await _approved_listing(61, "10", "5")

    async with async_session_maker() as session:
        tx = await MarketplaceService.purchase(session, listing_id, 62)
        tx_id = tx.id
        assert await MarketplaceService.dispute(session, tx_id, buyer_id=62, note="لم يصل")

    async with async_session_maker() as session:
        assert await MarketplaceService.release(session, tx_id, admin_id=1) is False

    buyer_before = await _balance(62)
    async with async_session_maker() as session:
        assert await MarketplaceService.refund(session, tx_id, admin_id=1) is True
    assert await _balance(62) == buyer_before + Decimal("10.5000")


@pytest.mark.asyncio
async def test_refund_returns_listing_to_market_before_expiry():
    await FeatureService.sync_registry()
    await _make_user(63, balance="20")
    await _make_user(64, balance="100")
    listing_id = await _approved_listing(63, "10", "5")

    async with async_session_maker() as session:
        tx = await MarketplaceService.purchase(session, listing_id, 64)
        tx_id = tx.id
    async with async_session_maker() as session:
        await MarketplaceService.refund(session, tx_id, admin_id=1)
        listing = await session.get(MarketListing, listing_id)
    assert listing.status == MarketListingStatus.APPROVED


@pytest.mark.asyncio
async def test_seller_cannot_buy_own_listing():
    await FeatureService.sync_registry()
    await _make_user(71, balance="500")
    listing_id = await _approved_listing(71, "10", "5")
    async with async_session_maker() as session:
        with pytest.raises(MarketError):
            await MarketplaceService.purchase(session, listing_id, 71)


@pytest.mark.asyncio
async def test_disabling_marketplace_blocks_new_listings():
    await FeatureService.sync_registry()
    await _make_user(72, balance="20")
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "peer_marketplace", False)
        with pytest.raises(MarketError):
            await MarketplaceService.create_listing(
                session, 72, "service", "x", "y", Decimal("10")
            )
        await FeatureService.set_enabled(session, "peer_marketplace", True)


# ═══════════════════════════ التحقق من آيدي اللاعب ═══════════════════════════


@pytest.mark.asyncio
async def test_player_id_rejects_links_usernames_and_spaces():
    await FeatureService.sync_registry()
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "player_id_validation", True)

    for bad in ("https://pubg.com/x", "@mohammed_99", "1234 5678", ""):
        with pytest.raises(PlayerIdError):
            await PlayerIdService.validate(bad, context="ببجي")


@pytest.mark.asyncio
async def test_player_id_normalizes_arabic_digits_and_accepts_valid():
    await FeatureService.sync_registry()
    cleaned = await PlayerIdService.validate("٥١٢٣٤٥٦٧٨٩", context="ببجي")
    assert cleaned == "5123456789"

    # آيدي قصير جداً لببجي يجب أن يُرفض
    with pytest.raises(PlayerIdError):
        await PlayerIdService.validate("123", context="ببجي")


@pytest.mark.asyncio
async def test_player_id_strict_mode_can_be_disabled_from_admin():
    await FeatureService.sync_registry()
    async with async_session_maker() as session:
        await FeatureService.set_option(
            session, "player_id_validation", "strict_format", False
        )
    # مع تعطيل الصرامة يقبل آيدي غير مطابق لنمط لعبة محددة
    assert await PlayerIdService.validate("ABC123XYZ", context="لعبة غير معروفة") == "ABC123XYZ"
    async with async_session_maker() as session:
        await FeatureService.set_option(session, "player_id_validation", "strict_format", True)
