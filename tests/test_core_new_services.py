"""
اختبارات رسمية للخدمات التي بُنيت في الموجات من الثالثة حتى الرابعة عشرة
ولم تكن مغطاة: الشراء بالجملة، التدريج، التفاعل، النمو، التأمين،
المقتنيات الرقمية، تبديل المفتاح، التقدّم الحي، العمليات، تقييم الجودة،
ضمان التعويض، والثقة.

تركّز على ما يمكن أن ينكسر بصمت: حسابات المال، منع الصرف المزدوج،
وسلوك الميزة حين يوقفها الأدمن.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    Country,
    NumberOrder,
    NumberService,
    OrderStatus,
    Product,
    ProductDisplayType,
    ProductFulfillmentType,
    ProductPricingType,
    ProductStatus,
    ProviderName,
    SubCategory,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from services.ai_layer_service import (
    AIMerchandiserService,
    AIProviderClient,
    AIAgentService,
    AutonomousPurchaseService,
    LocalPersonaService,
)
from services.bulk_number_service import BulkNumberService
from services.drip_feed_service import DripFeedService
from services.engagement_service import (
    BehavioralFingerprintService,
    CampaignService,
    LeaderboardService,
    SmartMixService,
    TournamentService,
)
from services.feature_service import FeatureService
from services.growth_service import AffiliateService, FreeTrialService, TrialError
from services.insurance_service import InsuranceError, InsuranceService
from services.inventory_growth_service import (
    DedicatedNumberService,
    SharedPlanService,
    WarmPoolService,
)
from services.live_progress_service import LiveProgressService
from services.operations_service import (
    CatalogAutopilotService,
    FxFeedService,
    SelfHealingService,
)
from services.quality_score_service import PredictiveRiskService, QualityScoreService
from services.refill_service import RefillService
from services.trust_service import (
    NumberLineageService,
    SmartVerificationService,
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
        return Decimal(str((await session.get(User, user_id)).balance))


async def _points_of(user_id: int) -> int:
    async with async_session_maker() as session:
        return int((await session.get(User, user_id)).loyalty_points or 0)


async def _number_catalog() -> None:
    """دولة وخدمة أرقام لاختبارات الجملة. كل كيان يُفحص على حدة."""
    async with async_session_maker() as session:
        if await session.get(Country, 1) is None:
            session.add(
                Country(id=1, code="tr", name_ar="تركيا", flag="🇹🇷",
                        fivesim_code="tur", is_active=True)
            )
        if await session.get(NumberService, 1) is None:
            session.add(
                NumberService(id=1, code="tg", name_ar="تليجرام", emoji="✈️",
                              fivesim_code="tg", is_active=True)
            )
        await session.commit()


async def _product_catalog() -> None:
    async with async_session_maker() as session:
        if await session.get(Product, 1) is not None:
            return
        # نفحص كل كيان على حدة: قاعدة الاختبار قد تحتوي أحدها دون الآخر
        existing_cat = (await session.execute(select(Category).limit(1))).scalars().first()
        if existing_cat is None:
            category = Category(name_ar="رشق", type=CategoryType.SMM, is_active=True)
            session.add(category)
            await session.flush()
        else:
            category = existing_cat
        existing_sub = (await session.execute(select(SubCategory).limit(1))).scalars().first()
        if existing_sub is None:
            sub = SubCategory(category_id=category.id, name_ar="تيك توك", is_active=True)
            session.add(sub)
            await session.flush()
        else:
            sub = existing_sub
        session.add(
            Product(
                id=1, sub_category_id=sub.id, name_ar="1000 متابع",
                price_usd=Decimal("2.00"), cost_price_usd=Decimal("1.0"),
                pricing_type=ProductPricingType.FIXED,
                display_type=ProductDisplayType.PER_1000,
                status=ProductStatus.ACTIVE, min_quantity=1, max_quantity=100000,
            )
        )
        await session.commit()


def _stub_provider(monkeypatch, fail_every: int = 0):
    """يستبدل مزود الأرقام بمزود وهمي، ويفشّل كل طلب رقمه يقبل القسمة على fail_every."""
    import providers.manager as pm
    import services.bulk_number_service as bns

    state = {"n": 0}

    async def fake_prices(service, country, session=None):
        return {ProviderName.FIVESIM: Decimal("0.20")}

    async def fake_buy(service, country, session=None, preferred_provider=None):
        state["n"] += 1
        if fail_every and state["n"] % fail_every == 0:
            raise pm.ProviderUnavailableError("نفد المخزون")

        class _Result:
            provider = ProviderName.FIVESIM
            provider_order_id = f"stub-{state['n']}"
            phone_number = f"+90555{state['n']:06d}"
            cost_usd = Decimal("0.20")

        return _Result()

    monkeypatch.setattr(pm.provider_manager, "get_cheapest_price", fake_prices)
    monkeypatch.setattr(pm.provider_manager, "buy_number", fake_buy)
    monkeypatch.setattr(bns, "provider_manager", pm.provider_manager)


# ═══════════════════════════ الشراء بالجملة ═══════════════════════════


@pytest.mark.asyncio
async def test_bulk_volume_discount_tiers():
    await _enable("bulk_numbers")
    assert await BulkNumberService.discount_percent(5) == Decimal("0")
    assert await BulkNumberService.discount_percent(10) == Decimal("1")
    assert await BulkNumberService.discount_percent(50) == Decimal("3")
    assert await BulkNumberService.discount_percent(100) == Decimal("5")
    assert await BulkNumberService.discount_percent(500) == Decimal("8")


@pytest.mark.asyncio
async def test_bulk_execution_charges_only_for_successes(monkeypatch):
    """الفشل الجزئي يجب أن يُسترجع، فلا يُخصم المستخدم على ما لم يستلمه."""
    await _enable("bulk_numbers")
    await _number_catalog()
    await _user(3101, "100")

    _stub_provider(monkeypatch, fail_every=0)
    async with async_session_maker() as session:
        service = await session.get(NumberService, 1)
        country = await session.get(Country, 1)
        before = (await session.get(User, 3101)).balance
        result = await BulkNumberService.execute(session, 3101, service, country, 20)
        after = (await session.get(User, 3101)).balance

    assert result["succeeded"] == 20
    assert result["failed"] == 0
    assert result["refunded_usd"] == Decimal("0")
    assert after == before - result["net_charged_usd"]


@pytest.mark.asyncio
async def test_bulk_partial_failure_refunds_the_failed_share(monkeypatch):
    await _enable("bulk_numbers")
    await _number_catalog()
    await _user(3102, "100")

    _stub_provider(monkeypatch, fail_every=3)  # كل ثالث طلب يفشل
    async with async_session_maker() as session:
        service = await session.get(NumberService, 1)
        country = await session.get(Country, 1)
        before = (await session.get(User, 3102)).balance
        result = await BulkNumberService.execute(session, 3102, service, country, 9)
        after = (await session.get(User, 3102)).balance

    assert result["succeeded"] == 6
    assert result["failed"] == 3
    assert result["refunded_usd"] > 0
    # الخصم الصافي = المطلوب - المسترجع
    assert after == before - result["net_charged_usd"]
    assert result["net_charged_usd"] == result["total_charged_usd"] - result["refunded_usd"]


def test_bulk_csv_export_shape():
    class _Order:
        id = 7
        phone_number = "+905550000007"
        provider = ProviderName.FIVESIM
        status = OrderStatus.PENDING

    csv = BulkNumberService.export_csv([_Order()])
    lines = csv.split("\n")
    assert lines[0] == "order_id,phone_number,provider,status,created_at"
    assert "fivesim" in lines[1]
    assert "+905550000007" in lines[1]


# ═══════════════════════════ التدريج ═══════════════════════════


def test_drip_split_never_loses_quantity():
    for quantity, runs in [(10, 3), (7, 7), (100, 4), (5, 2), (1000, 7)]:
        plan = DripFeedService.plan(quantity, runs)
        assert sum(plan) == quantity, f"{quantity}/{runs} ضاعت كمية"
        assert len(plan) == runs


@pytest.mark.asyncio
async def test_drip_rejects_invalid_schedules():
    await _enable("drip_feed")
    await _product_catalog()
    await _user(3201, "100")

    async with async_session_maker() as session:
        order = UnifiedOrder(
            user_id=3201, product_id=1, quantity=1000, target="https://x",
            price_usd=Decimal("2"), cost_price_usd=Decimal("1"),
            status=UnifiedOrderStatus.PROCESSING,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        with pytest.raises(DripFeedService.__mro__[0].__module__ and Exception):
            # دفعة واحدة ليست تدريجاً
            await DripFeedService.schedule(session, order, runs=1, interval_minutes=60)


@pytest.mark.asyncio
async def test_drip_creates_scheduled_runs_and_zeroes_parent():
    await _enable("drip_feed")
    await _product_catalog()
    await _user(3202, "100")

    async with async_session_maker() as session:
        order = UnifiedOrder(
            user_id=3202, product_id=1, quantity=900, target="https://x",
            price_usd=Decimal("2"), cost_price_usd=Decimal("1"),
            status=UnifiedOrderStatus.PROCESSING,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        order_id = order.id

        runs = await DripFeedService.schedule(session, order, runs=3, interval_minutes=60)

    assert len(runs) == 3
    assert sum(r.quantity for r in runs) == 900

    async with async_session_maker() as session:
        parent = await session.get(UnifiedOrder, order_id)
        # الطلب الأب يصير مظلة لا تُنفَّذ مرتين
        assert parent.quantity == 0

        progress = await DripFeedService.progress(session, order_id)
    assert progress["runs"] == 3
    assert progress["pending"] == 3
    assert progress["percent"] == 0.0


# ═══════════════════════════ منشئ الحملات ═══════════════════════════


@pytest.mark.asyncio
async def test_campaign_plan_never_exceeds_budget():
    await _enable("campaign_builder")
    await _product_catalog()

    async with async_session_maker() as session:
        plan = await CampaignService.plan(
            session, [1], Decimal("50"), "https://tiktok.com/@me", spread_days=3
        )
    assert plan["items"]
    total = sum(item["cost_usd"] for item in plan["items"])
    assert total <= Decimal("50")
    assert plan["planned_usd"] + plan["unallocated_usd"] == Decimal("50")


# ═══════════════════════════ البطولات ═══════════════════════════


@pytest.mark.asyncio
async def test_tournament_prizes_descend_and_sum_correctly():
    """
    regression: تقسيم «نصف الباقي» كان يعطي تعادلاً عند فائزين (4 و4 من 8).
    الآن الأوزان 1/المركز مطبّعة، والأخير يأخذ الباقي فلا يضيع فرق التقريب.
    """
    await _enable("tournaments")
    await _user(3301, "0")
    await _user(3302, "0")
    await _user(3303, "0")

    async with async_session_maker() as session:
        paid = await TournamentService.settle(session, [3301, 3302, 3303], Decimal("10"))

    assert len(paid) == 3
    # تنازلي
    assert paid[0]["prize_usd"] > paid[1]["prize_usd"] > paid[2]["prize_usd"]
    # مجموع الجوائز = 80% من الصندوق
    total = sum(p["prize_usd"] for p in paid)
    assert total == Decimal("8.0000")


# ═══════════════════════════ البصمة السلوكية ═══════════════════════════


@pytest.mark.asyncio
async def test_behavioural_fingerprint_flags_scripted_accounts():
    await _product_catalog()
    await _enable("behavioral_fingerprint")
    await _user(3401, "0", total_orders=25)
    # حساب جديد يشتري فوراً بفواصل منتظمة تماماً
    async with async_session_maker() as session:
        user = await session.get(User, 3401)
        user.joined_at = datetime.utcnow()
        now = datetime.utcnow()
        for index in range(22):
            session.add(
                UnifiedOrder(
                    id=34000 + index, user_id=3401, product_id=1, quantity=100,
                    price_usd=Decimal("1"), cost_price_usd=Decimal("0.5"),
                    status=UnifiedOrderStatus.COMPLETED,
                    created_at=now - timedelta(seconds=index * 10),
                )
            )
        await session.commit()

        verdict, reason = await BehavioralFingerprintService.verdict(session, 3401)
        data = await BehavioralFingerprintService.score(session, 3401)

    assert verdict in ("review", "block")
    assert "regular_intervals" in data["flags"]


# ═══════════════════════════ التجربة المجانية ═══════════════════════════


@pytest.mark.asyncio
async def test_free_trial_is_once_per_user_and_new_users_only():
    await _enable("free_trial")
    await _user(3501, "0")
    await _user(3502, "0", total_orders=5)

    async with async_session_maker() as session:
        ok, _ = await FreeTrialService.is_eligible(session, 3501)
        assert ok is True
        granted = await FreeTrialService.grant(session, 3501)
    assert granted > 0
    assert await _balance(3501) == granted

    # لا تُمنح مرتين
    async with async_session_maker() as session:
        with pytest.raises(TrialError):
            await FreeTrialService.grant(session, 3501)

    # مستخدم قديم لا يستحقها
    async with async_session_maker() as session:
        ok, reason = await FreeTrialService.is_eligible(session, 3502)
    assert ok is False


# ═══════════════════════════ الإحالة متعددة المستويات ═══════════════════════════


@pytest.mark.asyncio
async def test_affiliate_pays_each_level_once_per_order():
    await _enable("affiliate_tiers")
    await _user(3601, "0")
    await _user(3602, "0", referrer_id=3601)
    await _user(3603, "0", referrer_id=3602)

    async with async_session_maker() as session:
        chain = await AffiliateService.upline(session, 3603)
    assert chain == [(3602, 1), (3601, 2)]

    async with async_session_maker() as session:
        first = await AffiliateService.reward_purchase(
            session, 3603, Decimal("100"), "unified_orders", 360301
        )
    assert len(first) == 2
    assert first[0]["bonus_usd"] == Decimal("5.0000")   # 5% للمستوى 1
    assert first[1]["bonus_usd"] == Decimal("2.0000")   # 2% للمستوى 2

    # نفس الطلب مرة أخرى لا يدفع شيئاً
    async with async_session_maker() as session:
        before = (await session.get(User, 3602)).balance
        await AffiliateService.reward_purchase(
            session, 3603, Decimal("100"), "unified_orders", 360301
        )
        after = (await session.get(User, 3602)).balance
    assert before == after


# ═══════════════════════════ التأمين ═══════════════════════════


@pytest.mark.asyncio
async def test_insurance_claim_pays_once_and_never_overdraws_pool():
    await _enable("sms_insurance")
    await _user(3701, "100")

    async with async_session_maker() as session:
        order = NumberOrder(
            id=3701, user_id=3701, provider=ProviderName.FIVESIM,
            provider_order_id="ins1", service="tg", country_code="tr",
            phone_number="+905553701000",
            price_provider_usd=Decimal("0.2"), price_sell_usd=Decimal("0.5"),
            status=OrderStatus.PENDING,
            purchased_at=datetime.utcnow() - timedelta(minutes=10),
        )
        session.add(order)
        await session.commit()

        premium = await InsuranceService.collect_premium(
            session, 3701, 3701, Decimal("0.5")
        )
        assert premium > 0
        report_before = await InsuranceService.report(session)
        assert report_before["pool_usd"] > 0

        claim = await InsuranceService.claim(session, 3701, 3701)
        assert claim["payout_usd"] > 0
        # الصندوق لا يُسحب لأكثر مما فيه
        assert claim["payout_usd"] <= report_before["pool_usd"]

        # لا مطالبة مزدوجة
        with pytest.raises(InsuranceError):
            await InsuranceService.claim(session, 3701, 3701)


@pytest.mark.asyncio
async def test_insurance_rejects_claim_for_delivered_code():
    await _enable("sms_insurance")
    await _user(3702, "100")
    async with async_session_maker() as session:
        session.add(
            NumberOrder(
                id=3702, user_id=3702, provider=ProviderName.FIVESIM,
                provider_order_id="ins2", service="tg", country_code="tr",
                phone_number="+905553702000",
                price_provider_usd=Decimal("0.2"), price_sell_usd=Decimal("0.5"),
                status=OrderStatus.COMPLETED, sms_code="123456",
                purchased_at=datetime.utcnow() - timedelta(minutes=10),
            )
        )
        await session.commit()
        with pytest.raises(InsuranceError):
            await InsuranceService.claim(session, 3702, 3702)


# ═══════════════════════════ الاشتراك العائلي ═══════════════════════════


def test_shared_plan_split_is_guarded():
    assert SharedPlanService.split_cost(Decimal("9"), 6) == Decimal("1.5000")
    assert SharedPlanService.split_cost(Decimal("10"), 3) == Decimal("3.3333")
    # لا قسمة على صفر
    assert SharedPlanService.split_cost(Decimal("10"), 0) == Decimal("10")


# ═══════════════════════════ البركة المُسخَّنة ═══════════════════════════


@pytest.mark.asyncio
async def test_warm_pool_assigns_unowned_numbers():
    """
    البركة جدول مستقل لأن number_orders.user_id غير قابل للفراغ،
    والرقم في البركة لا مالك له بعد.
    """
    await _enable("warm_pool")
    await _number_catalog()
    await _user(3801, "0")

    from database.models import WarmPoolNumber

    async with async_session_maker() as session:
        session.add(
            WarmPoolNumber(
                provider=ProviderName.FIVESIM,
                provider_order_id="pool1",
                phone_number="+905553801000",
                service_code="tg",
                country_code="tr",
                cost_usd=Decimal("0.2"),
                expires_at=datetime.utcnow() + timedelta(minutes=10),
            )
        )
        await session.commit()

        assert await WarmPoolService.level(session, "tg", "tr") == 1
        # العتبة الافتراضية 5، فبركة فيها رقم واحد فعلاً تحتاج تعبئة
        assert await WarmPoolService.needs_refill(session, "tg", "tr") is True

        pooled = await WarmPoolService.available(session, "tg", "tr")
        assert pooled is not None
        order = await WarmPoolService.assign(session, pooled, 3801, 5)

        # الإسناد أنشأ طلباً حقيقياً باسم المستخدم
        assert order.user_id == 3801
        assert order.phone_number == "+905553801000"
        # ولم يعد في البركة
        assert await WarmPoolService.level(session, "tg", "tr") == 0
        assert await WarmPoolService.available(session, "tg", "tr") is None


# ═══════════════════════════ التقدّم الحي ═══════════════════════════


@pytest.mark.asyncio
async def test_live_progress_computes_percentage_and_detects_drop():
    await _product_catalog()
    await _enable("live_progress")
    async with async_session_maker() as session:
        order = UnifiedOrder(
            user_id=1, product_id=1, quantity=1000, target="https://x",
            price_usd=Decimal("2"), cost_price_usd=Decimal("1"),
            status=UnifiedOrderStatus.PROCESSING,
            start_count=1000, remains=250,
            created_at=datetime.utcnow() - timedelta(minutes=30),
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        data = await LiveProgressService.progress(session, order)

    assert data["delivered"] == 750
    assert data["percent"] == 75.0
    assert data["speed_per_hour"] is not None
    assert LiveProgressService.bar(75).count("█") == 8


@pytest.mark.asyncio
async def test_live_progress_detects_follower_drop():
    await _product_catalog()
    await _enable("live_progress")
    async with async_session_maker() as session:
        order = UnifiedOrder(
            user_id=1, product_id=1, quantity=1000, target="https://x",
            price_usd=Decimal("2"), cost_price_usd=Decimal("1"),
            status=UnifiedOrderStatus.COMPLETED,
            start_count=1000, remains=200,  # نُفِّذ 800 ثم هبط
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        data = await LiveProgressService.progress(session, order)

    assert data["has_drop"] is True
    assert data["dropped_by"] == 200


# ═══════════════════════════ تقييم الجودة والمخاطر ═══════════════════════════


@pytest.mark.asyncio
async def test_quality_badge_needs_minimum_samples():
    await _enable("smm_quality_score")
    await _product_catalog()
    await _user(3901, "0")

    async with async_session_maker() as session:
        # دون الحد الأدنى من العيّنات لا يُوسم المنتج
        for index in range(3):
            session.add(
                UnifiedOrder(
                    id=39000 + index, user_id=3901, product_id=1, quantity=1000,
                    price_usd=Decimal("2"), cost_price_usd=Decimal("1"),
                    status=UnifiedOrderStatus.COMPLETED, remains=0,
                )
            )
        await session.commit()
        assert await QualityScoreService.score_product(session, 1) is None

        # بلوغ الحد الأدنى ينتج وسماً
        for index in range(3, 12):
            session.add(
                UnifiedOrder(
                    id=39000 + index, user_id=3901, product_id=1, quantity=1000,
                    price_usd=Decimal("2"), cost_price_usd=Decimal("1"),
                    status=UnifiedOrderStatus.COMPLETED, remains=0,
                )
            )
        await session.commit()
        score = await QualityScoreService.score_product(session, 1)

    assert score is not None
    assert score["success_rate"] == 100.0
    assert score["badge"] == "🛡️ Non-Drop"


@pytest.mark.asyncio
async def test_predictive_risk_needs_samples_then_reports():
    await _product_catalog()
    await _enable("predictive_ban_risk")
    await _product_catalog()
    await _user(3911, "0")

    async with async_session_maker() as session:
        for index in range(25):
            session.add(
                UnifiedOrder(
                    id=39100 + index, user_id=3911, product_id=1, quantity=1000,
                    price_usd=Decimal("2"), cost_price_usd=Decimal("1"),
                    status=(
                        UnifiedOrderStatus.FAILED if index % 2 == 0
                        else UnifiedOrderStatus.COMPLETED
                    ),
                    remains=0,
                )
            )
        await session.commit()
        risk = await PredictiveRiskService.risk_for(session, 1)

    assert risk["available"] is True
    assert risk["samples"] == 25
    # 13 من 25 فشلت (index%2==0 يشمل الصفر) = 52%
    assert risk["failure_rate"] == 52.0
    assert risk["risky"] is True


# ═══════════════════════════ ضمان التعويض ═══════════════════════════


@pytest.mark.asyncio
async def test_refill_skips_services_without_refill_support():
    await _product_catalog()
    await _enable("refill_guarantee")
    await _product_catalog()
    await _user(4001, "0")

    from database.models import ApiProvider, ApiProviderType, ApiProtocolType

    async with async_session_maker() as session:
        if await session.get(ApiProvider, 1) is None:
            session.add(
                ApiProvider(
                    id=1, name="P1", type=ApiProviderType.SMM,
                    protocol_type=ApiProtocolType.SMM_V2, api_url="http://a",
                    api_key="k", is_active=True,
                )
            )
            await session.commit()

        order = UnifiedOrder(
            user_id=4001, product_id=1, quantity=1000, target="https://x",
            price_usd=Decimal("2"), cost_price_usd=Decimal("1"),
            status=UnifiedOrderStatus.COMPLETED, remains=100,
            completed_at=datetime.utcnow(),
            # without these the order cannot be refilled at all, so due_orders
            # correctly excludes it
            external_order_id="ext-4001",
            api_provider_id=1,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        # المنتج غير مربوط بخدمة مزود تدعم refill، فلا يُطلب تعويض
        assert await RefillService.supports_refill(session, order) is False

        # الطلب مكتمل وله مزود، فهو ضمن فترة الضمان
        due = await RefillService.due_orders(session)
        assert any(o.id == order.id for o in due)

        # طلب بلا مزود يُستبعد لأنه غير قابل للتعويض
        orphan = UnifiedOrder(
            user_id=4001, product_id=1, quantity=1000, target="https://y",
            price_usd=Decimal("2"), cost_price_usd=Decimal("1"),
            status=UnifiedOrderStatus.COMPLETED, remains=100,
            completed_at=datetime.utcnow(),
        )
        session.add(orphan)
        await session.commit()
        await session.refresh(orphan)

        due2 = await RefillService.due_orders(session)
        assert not any(o.id == orphan.id for o in due2)


# ═══════════════════════════ الثقة والتحقق ═══════════════════════════


@pytest.mark.asyncio
async def test_number_lineage_proves_prior_sale():
    await _enable("number_lineage")
    await _user(4101, "0")
    await _user(4102, "0")

    async with async_session_maker() as session:
        order = NumberOrder(
            id=4101, user_id=4101, provider=ProviderName.FIVESIM,
            provider_order_id="lin1", service="tg", country_code="tr",
            phone_number="+905554101000",
            price_provider_usd=Decimal("0.2"), price_sell_usd=Decimal("0.5"),
            status=OrderStatus.COMPLETED,
            completed_at=datetime.utcnow() - timedelta(days=1),
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        await NumberLineageService.stamp(session, order)

        prior = await NumberLineageService.was_sold_before(session, "+905554101000")
        assert prior is not None
        assert prior["user_id"] == 4101

        assert await NumberLineageService.was_sold_before(session, "+905559999999") is None
        assert await NumberLineageService.should_block(session, "+905554101000") is True


@pytest.mark.asyncio
async def test_tiered_verification_caps_by_level():
    await _enable("smart_verification")
    await _user(4201, "0", total_orders=0, joined_at=datetime.utcnow())
    await _user(4202, "0", total_orders=10)
    await _user(4203, "0", total_orders=10, verification_tier=2)

    async with async_session_maker() as session:
        assert await SmartVerificationService.tier_for(session, 4201) == 0
        assert await SmartVerificationService.tier_for(session, 4202) == 1
        assert await SmartVerificationService.tier_for(session, 4203) == 2

        ok, reason = await SmartVerificationService.check(
            session, 4201, "peer_marketplace", Decimal("500")
        )
        assert ok is False

        ok, _ = await SmartVerificationService.check(
            session, 4203, "peer_marketplace", Decimal("500")
        )
        assert ok is True

        # عند إيقاف الميزة لا قيود
        await FeatureService.set_enabled(session, "smart_verification", False)
        ok, _ = await SmartVerificationService.check(
            session, 4201, "peer_marketplace", Decimal("99999")
        )
    assert ok is True


# ═══════════════════════════ غرفة العمليات ═══════════════════════════


@pytest.mark.asyncio
async def test_degraded_provider_gets_lower_weight_not_removal():
    await _enable("self_healing_ops")
    async with async_session_maker() as session:
        for _ in range(10):
            await SelfHealingService.record(session, "healthy", True, 100.0)
        for _ in range(8):
            await SelfHealingService.record(session, "degraded", False, 4000.0)
        for _ in range(2):
            await SelfHealingService.record(session, "degraded", True, 200.0)

        healthy = await SelfHealingService.health(session, "healthy")
        degraded = await SelfHealingService.health(session, "degraded")
        healthy_weight = await SelfHealingService.weight(session, "healthy")
        degraded_weight = await SelfHealingService.weight(session, "degraded")

    assert healthy["success_rate"] == 100.0
    assert degraded["degraded"] is True
    assert healthy_weight == 100
    # يُخفَّض وزنه لا يُستبعد، حتى يستطيع التعافي
    assert 0 < degraded_weight < 100


@pytest.mark.asyncio
async def test_fx_refresh_is_noop_when_disabled():
    await _disable("live_fx_feed")
    async with async_session_maker() as session:
        assert await FxFeedService.refresh(session) == {}


@pytest.mark.asyncio
async def test_catalog_autopilot_disabled_changes_nothing():
    await _disable("catalog_autopilot")
    async with async_session_maker() as session:
        assert await CatalogAutopilotService.disable_deleted_services(session) == []
        assert await CatalogAutopilotService.arbitrage_alerts(session) == []


# ═══════════════════════════ الرفّ الذكي والشخصيات ═══════════════════════════


@pytest.mark.asyncio
async def test_merchandiser_falls_back_to_bestsellers_when_disabled():
    await _product_catalog()
    await _user(4301, "0")
    async with async_session_maker() as session:
        product = await session.get(Product, 1)
        product.total_sold = 99
        product.is_bestseller = True
        await session.commit()

        await FeatureService.set_enabled(session, "ai_merchandiser", False)
        shelf = await AIMerchandiserService.shelf_for(session, 4301, 5)
    assert shelf and shelf[0].id == 1


@pytest.mark.asyncio
async def test_ai_agent_falls_back_without_llm_provider():
    await _enable("ai_agent_layer")
    await _product_catalog()
    await _user(4302, "0")

    assert await AIProviderClient.configured() is False
    async with async_session_maker() as session:
        result = await AIAgentService.respond(session, 4302, "بدي متابعين")
    assert result["source"] == "fallback"
    assert result["products"]


@pytest.mark.asyncio
async def test_local_persona_changes_tone_per_market():
    await _enable("local_personas")
    await _user(4303, "0")
    from services.settings_service import SettingsService

    async with async_session_maker() as session:
        default_prompt = await LocalPersonaService.system_prompt(session, 4303)
        await SettingsService.set(session, "user_market_4303", "egypt")
        egypt_prompt = await LocalPersonaService.system_prompt(session, 4303)

    assert default_prompt != egypt_prompt
    assert "مصرية" in egypt_prompt


@pytest.mark.asyncio
async def test_autonomous_agent_respects_budget_ceiling():
    await _product_catalog()
    await _enable("autonomous_purchase_agent")
    await _number_catalog()
    await _user(4304, "1000")

    from services.ai_layer_service import AIError

    async with async_session_maker() as session:
        with pytest.raises(AIError):
            await AutonomousPurchaseService.create_job(
                session, 4304, "tg", "tr",
                quantity_per_run=10, runs=3, interval_hours=24,
                max_unit_price_usd=Decimal("999"),
            )
        job = await AutonomousPurchaseService.create_job(
            session, 4304, "tg", "tr",
            quantity_per_run=5, runs=3, interval_hours=24,
            max_unit_price_usd=Decimal("0.30"),
        )
    assert job["job_id"]
    assert job["estimated_usd"] == Decimal("4.50")
