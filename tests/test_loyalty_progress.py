"""لوحة تقدّم سلسلة الولاء كما يراها الزبون.

الزبون يجب أن يعرف: كم طلب باقٍ له، وماذا سيكسب — لا أن يُفاجأ
بكوبون يصل دون سبب واضح. هذه اللوحة تُعرض داخل شاشة الولاء.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import UnifiedOrder, UnifiedOrderStatus, User


async def _seed_user_with_orders(telegram_id: int, orders: int) -> int:
    async with async_session_maker() as session:
        user = User(telegram_id=telegram_id, username=f"chain{telegram_id}", balance=Decimal("10"))
        session.add(user)
        await session.flush()
        now = datetime.utcnow()
        for _ in range(orders):
            session.add(
                UnifiedOrder(
                    user_id=user.id,
                    price_usd=Decimal("2.00"),
                    status=UnifiedOrderStatus.COMPLETED,
                    created_at=now,
                )
            )
        await session.commit()
        return int(user.id)


@pytest.mark.asyncio
async def test_chain_progress_shows_remaining_orders():
    """طلبان ⇒ «باقي طلب واحد وتفتح لك ٥٪»."""
    from services.loyalty_chain_service import LoyaltyChainService

    user_id = await _seed_user_with_orders(6100001, orders=2)

    async with async_session_maker() as session:
        data = await LoyaltyChainService.progress(session, user_id)

    assert data is not None
    assert data["orders"] == 2
    assert data["next_tier"] == (3, 5)
    assert data["remaining"] == 1
    assert data["current_percent"] == 0
    assert data["coupons"] == []

    text = LoyaltyChainService.render_progress(data, "ar")
    assert "سلسلة الولاء" in text
    assert "باقي" in text
    assert "5٪" in text
    assert LoyaltyChainService.render_progress(None, "ar") == ""


@pytest.mark.asyncio
async def test_chain_progress_shows_earned_coupon_and_next_step(monkeypatch):
    """بعد العتبة الأولى: الكوبون يظهر، والهدف التالي يتحدث إلى ١٠٪."""

    class FakeNotifier:
        def __init__(self, bot):
            pass

        async def notify_user(self, telegram_id, text, **kwargs):
            return True

    import services.notification_service as notification_module

    monkeypatch.setattr(notification_module, "NotificationService", FakeNotifier)

    from services.loyalty_chain_service import LoyaltyChainService

    user_id = await _seed_user_with_orders(6100002, orders=3)
    granted = await LoyaltyChainService.grant(bot=object())
    assert granted == 1

    async with async_session_maker() as session:
        data = await LoyaltyChainService.progress(session, user_id)

    assert data["orders"] == 3
    assert data["earned"] == [(3, 5)]
    assert data["current_percent"] == 5
    assert data["next_tier"] == (6, 10)
    assert data["remaining"] == 3
    assert len(data["coupons"]) == 1
    assert data["coupons"][0].code.startswith("LOY")

    text = LoyaltyChainService.render_progress(data, "ar")
    assert data["coupons"][0].code in text
    assert "10٪" in text
    assert "3" in text, "عدد الطلبات المتبقية يجب أن يظهر"


@pytest.mark.asyncio
async def test_chain_progress_english_and_top_tier():
    """الإنجليزية مدعومة، وأعلى عتبة تُخبر الزبون أنه وصل القمة."""
    from services.loyalty_chain_service import LoyaltyChainService

    user_id = await _seed_user_with_orders(6100003, orders=12)

    async with async_session_maker() as session:
        data = await LoyaltyChainService.progress(session, user_id)

    assert data["next_tier"] is None, "١٢ طلباً تتجاوز أعلى عتبة"
    assert LoyaltyChainService.render_progress(data, "ar").count("👑") == 1
    english = LoyaltyChainService.render_progress(data, "en")
    assert "Loyalty streak" in english

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        assert user is not None


class _FakeMessage:
    """رسالة وهمية تلتقط النص المعروض."""

    def __init__(self):
        self.text = None
        self.reply_markup = None

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self.text = text
        self.reply_markup = reply_markup


class _FakeTarget:
    """هدف وهمي: يلتقط النص سواء عُرض بتعديل رسالة أو برسالة جديدة."""

    def __init__(self):
        self.message = _FakeMessage()

    async def answer(self, text, reply_markup=None, **kwargs):
        self.message.text = text
        self.message.reply_markup = reply_markup


@pytest.mark.asyncio
async def test_loyalty_screen_shows_chain_block_and_shop_button():
    """شاشة الولاء تعرض تقدّم السلسلة وزر «أكمل السلسلة»."""
    from handlers.loyalty import _render_loyalty

    user_id = await _seed_user_with_orders(6100004, orders=1)

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        target = _FakeTarget()
        await _render_loyalty(target, session, user)

    text = target.message.text or ""
    assert "سلسلة الولاء" in text, "قسم سلسلة الولاء غائب عن شاشة الولاء"
    assert "باقي" in text
    buttons = [
        button.callback_data
        for row in (target.message.reply_markup.inline_keyboard if target.message.reply_markup else [])
        for button in row
    ]
    assert "store:home" in buttons, "زر إكمال السلسلة غائب"
