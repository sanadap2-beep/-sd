"""إضافات النمو الثلاث: العملاء النائمون + كوبون أول إيداع + صحة المزودين.

1) النائمون: تذكير «اشتقنا لك» يُرسل مرة واحدة ثم يسكت حتى تنقضي التهدئة.
2) الكوبون الترحيبي: يُنشأ عند أول إيداع مقبول فقط، وفوق حد أدنى.
3) صحة المزودين: تنبيه **قبل** نفاد الرصيد (مدة الصمود + مستويات خطورة).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    DepositRequest,
    DepositStatus,
    ProviderName,
    ProviderStatus,
    User,
)


async def _make_user(telegram_id: int, joined_days_ago: int | None = None) -> int:
    async with async_session_maker() as session:
        user = User(telegram_id=telegram_id, username=f"u{telegram_id}", balance=Decimal("10"))
        if joined_days_ago is not None:
            user.joined_at = datetime.utcnow() - timedelta(days=joined_days_ago)
        session.add(user)
        await session.commit()
        return int(user.id)


@pytest.mark.asyncio
async def test_welcome_coupon_created_once_for_first_deposit():
    """أول إيداع مقبول = كوبون واحد. الإيداع الثاني لا يكرره."""
    from services.coupon_service import CouponService
    from services.welcome_coupon_service import WelcomeCouponService

    user_id = await _make_user(5900001)

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        deposit = DepositRequest(
            user_id=user_id,
            amount_usd=Decimal("20"),
            status=DepositStatus.APPROVED,
        )
        session.add(deposit)
        await session.commit()
        coupon = await WelcomeCouponService.grant_for_first_deposit(
            session, user=user, deposit=deposit
        )
        assert coupon is not None, "أول إيداع مقبول يجب أن يمنح كوبوناً"
        assert coupon.max_uses == 1
        assert str(coupon.discount_type) == "percent"
        assert Decimal(str(coupon.discount_value)) > 0

        fetched = await CouponService.get_coupon_by_code(session, coupon.code)
        assert fetched is not None and fetched.id == coupon.id

        # إيداع ثانٍ: لا كوبون جديد
        second = DepositRequest(
            user_id=user_id,
            amount_usd=Decimal("50"),
            status=DepositStatus.APPROVED,
        )
        session.add(second)
        await session.commit()
        again = await WelcomeCouponService.grant_for_first_deposit(
            session, user=user, deposit=second
        )
        assert again is None, "الكوبون الترحيبي مرة واحدة لا أكثر"


@pytest.mark.asyncio
async def test_welcome_coupon_respects_minimum_deposit():
    """إيداع دون الحد الأدنى لا يستحق كوبوناً."""
    from services.welcome_coupon_service import WelcomeCouponService

    user_id = await _make_user(5900002)
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        tiny = DepositRequest(
            user_id=user_id, amount_usd=Decimal("1"), status=DepositStatus.APPROVED
        )
        session.add(tiny)
        await session.commit()
        assert await WelcomeCouponService.grant_for_first_deposit(session, user, tiny) is None


@pytest.mark.asyncio
async def test_dormant_reminder_sent_once_then_silent(monkeypatch):
    """العميل الغائب يُذكَّر مرة واحدة، والدورة الثانية لا تكرر."""
    from services.dormant_user_service import DormantUserService

    sent: list[tuple[int, str]] = []

    class FakeNotifier:
        def __init__(self, bot):
            pass

        async def notify_user(self, telegram_id, text, **kwargs):
            sent.append((telegram_id, text))
            return True

    import services.notification_service as notification_module

    monkeypatch.setattr(notification_module, "NotificationService", FakeNotifier)

    await _make_user(5900003, joined_days_ago=80)  # نائم
    await _make_user(5900004, joined_days_ago=1)   # جديد: لا يُذكَّر

    first = await DormantUserService.cycle(bot=object())
    assert first == 1, "يجب إرسال تذكير واحد للعميل النائم"
    assert sent and "اشتقنا" in sent[0][1]

    second = await DormantUserService.cycle(bot=object())
    assert second == 0, "التذكير تكرر = إزعاج"
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_provider_health_warns_before_balance_runs_out():
    """رصيد فوق الحد لكنه يقترب ⇒ تنبيه مبكر + مدة الصمود، لا صمت."""
    from services.provider_health_service import ProviderHealthService

    async with async_session_maker() as session:
        status = await session.get(ProviderStatus, ProviderName.FIVESIM)
        if status is None:
            status = ProviderStatus(provider=ProviderName.FIVESIM)
            session.add(status)
        status.balance = Decimal("13")  # الحد 10 ⇒ «early»
        status.is_online = True
        status.last_checked_at = datetime.utcnow()
        await session.commit()

    async with async_session_maker() as session:
        rows = await ProviderHealthService.snapshot(session, refresh=False)
        fivesim = next(row for row in rows if row["name"] == ProviderName.FIVESIM.value)
        assert fivesim["level"] == "early", "يجب التحذير قبل النفاد لا بعده"
        block = ProviderHealthService.render_block(rows)
        assert "يقترب من الحد" in block
        alert = ProviderHealthService.compose_alert(fivesim)
        assert "تنبيه مبكر" in alert

    assert ProviderHealthService.level(Decimal("0"), Decimal("10")) == "critical"
    assert ProviderHealthService.level(Decimal("5"), Decimal("10")) == "low"
    assert ProviderHealthService.level(Decimal("50"), Decimal("10")) == "ok"
    assert ProviderHealthService.level(None, Decimal("10")) == "unknown"
