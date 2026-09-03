"""Tests for digital subscriptions with provider-balance-aware flow."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

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
    SubCategory,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from protocols.base import ProtocolBalance, ProtocolOrder


# ══════════════ الفيك ══════════════


class FakeBot:
    def __init__(self):
        self.sent = []
        self._id = 2000

    async def send_message(self, chat_id=None, text=None, **kwargs):
        self._id += 1
        self.sent.append(
            {"chat_id": chat_id, "text": text, "reply_markup": kwargs.get("reply_markup")}
        )
        return type("M", (), {"message_id": self._id})()

    async def edit_message_text(self, chat_id=None, message_id=None, text=None, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, "edited": True})
        return None


class FakeMessage:
    def __init__(self):
        self.sent = []
        self.text = None

    async def answer(self, text, **kwargs):
        self.sent.append({"text": text})

    async def edit_text(self, text, **kwargs):
        self.sent.append({"text": text, "edited": True})


class FakeCallback:
    def __init__(self):
        self.message = FakeMessage()
        self.answers = []
        self.data = ""

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


class FakeState:
    def __init__(self, data=None):
        self._data = data or {}
        self.state = None
        self.cleared = False

    async def get_data(self):
        return dict(self._data)

    async def update_data(self, **kw):
        self._data.update(kw)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.cleared = True
        self._data = {}



class FakeTextMessage:
    def __init__(self, text):
        self.text = text
        self.caption = None
        self.sent = []

    async def answer(self, text, **kwargs):
        self.sent.append({"text": text})

class FakeProtocol:
    def __init__(self, balance_amount, place_result=None):
        self._balance = balance_amount
        self.place_result = place_result
        self.placed = []
        self.balance_calls = 0

    async def get_balance(self):
        self.balance_calls += 1
        return ProtocolBalance(amount=self._balance, currency="USD")

    async def place_order(self, service_id, target, quantity, extra_params=None):
        self.placed.append((service_id, target, quantity))
        return self.place_result


# ══════════════ الإعداد ══════════════


async def _make_setup(session, provider_balance_db, user_tg):
    provider = ApiProvider(
        name="Ggsoma",
        type=ApiProviderType.SMM,
        api_url="https://ggsoma.store/api/partner/v1",
        api_key="k",
        balance=provider_balance_db,
        rate_to_usd=Decimal("1"),
    )
    provider.custom_config = json.dumps({"engine": "ggsoma"})
    session.add(provider)
    await session.flush()

    category = Category(name_ar="اشتراكات", emoji="🛍", type=CategoryType.CUSTOM, is_active=True)
    session.add(category)
    await session.flush()
    sub = SubCategory(category_id=category.id, name_ar="Netflix", emoji="🎬", is_active=True)
    session.add(sub)
    await session.flush()
    product = Product(
        sub_category_id=sub.id,
        api_provider_id=provider.id,
        provider_service_id="1001",
        name_ar="Netflix 30 يوم",
        price_usd=Decimal("20"),
        cost_price_usd=Decimal("15"),
        pricing_type=ProductPricingType.FIXED,
        fulfillment_type=ProductFulfillmentType.API,
        min_quantity=1,
        max_quantity=1,
        requires_quantity=False,
        display_type=ProductDisplayType.FIXED_TOTAL,
        status=ProductStatus.ACTIVE,
    )
    session.add(product)
    await session.flush()

    user = User(
        telegram_id=user_tg,
        username=f"sub_{user_tg}",
        full_name="Sub Buyer",
        balance=Decimal("50"),
        is_activated=True,
    )
    session.add(user)
    await session.commit()
    for obj in (provider, category, sub, product, user):
        await session.refresh(obj)
    return provider, product, user


@pytest.mark.asyncio
async def test_insufficient_balance_manual_flow(monkeypatch):
    from handlers import games
    from protocols.factory import ProtocolFactory

    fake = FakeProtocol(balance_amount=Decimal("5"))
    monkeypatch.setattr(
        ProtocolFactory, "create_from_provider", staticmethod(lambda p: fake)
    )

    async with async_session_maker() as session:
        provider, product, user = await _make_setup(session, Decimal("5"), 9800001)
        provider_id, product_id, user_id = provider.id, product.id, user.id
        from services.settings_service import SettingsService

        await SettingsService.set(session, "large_order_confirm_usd", "100")

    bot = FakeBot()
    callback = FakeCallback()
    state = FakeState({"target": "buyer@gmail.com", "quantity": 1})

    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await games._execute_purchase(callback, session, db_user, bot, state, product_id, None)

    # لم يُرسل الطلب للمزود (رصيد غير كافٍ)
    assert fake.placed == []
    assert fake.balance_calls >= 1

    # المستخدم إشعر أن الكود سيصل خلال دقائق
    user_texts = [m["text"] for m in callback.message.sent if m.get("text")]
    assert any("سيصلك الكود/الحساب خلال دقائق" in t for t in user_texts)

    # قناة الإدارة: تنبيه بأزرار نعم/لا
    admin_msgs = [m for m in bot.sent if m["chat_id"] == -1001]
    alert = [m for m in admin_msgs if "رصيد المزود غير كافٍ" in m["text"]]
    assert alert, "لا يوجد تنبيه رصيد المزود"
    buttons = [b.callback_data for row in alert[0]["reply_markup"].inline_keyboard for b in row]

    async with async_session_maker() as session:
        order = (
            await session.execute(select(UnifiedOrder).where(UnifiedOrder.user_id == user_id))
        ).scalars().first()
        assert order is not None
        assert order.status == UnifiedOrderStatus.PENDING
        assert order.external_order_id is None
        assert "رصيد المزود غير كافٍ" in order.status_message
        assert f"admin:sub_send:{order.id}" in buttons
        assert f"admin:order_refund_ask:{order.id}" in buttons


@pytest.mark.asyncio
async def test_sufficient_balance_instant_delivery(monkeypatch):
    from handlers import games
    from protocols.factory import ProtocolFactory

    fake = FakeProtocol(
        balance_amount=Decimal("100"),
        place_result=ProtocolOrder(
            external_order_id="GGS-999",
            status="completed",
            raw={"delivery": {"content": "username: acc\npassword: pw"}},
        ),
    )
    monkeypatch.setattr(
        ProtocolFactory, "create_from_provider", staticmethod(lambda p: fake)
    )

    async with async_session_maker() as session:
        provider, product, user = await _make_setup(session, Decimal("100"), 9800002)
        provider_id, product_id, user_id = provider.id, product.id, user.id
        from services.settings_service import SettingsService

        await SettingsService.set(session, "large_order_confirm_usd", "100")

    bot = FakeBot()
    callback = FakeCallback()
    state = FakeState({"target": "buyer@gmail.com", "quantity": 1})

    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await games._execute_purchase(callback, session, db_user, bot, state, product_id, None)

    # تم إرسال الطلب للمزود وتسليم فوري
    assert len(fake.placed) == 1
    user_texts = [m["text"] for m in callback.message.sent if m.get("text")]
    assert any("تم التسليم فوراً" in t for t in user_texts)

    async with async_session_maker() as session:
        order = (
            await session.execute(select(UnifiedOrder).where(UnifiedOrder.user_id == user_id))
        ).scalars().first()
        assert order.status == UnifiedOrderStatus.COMPLETED
        assert order.external_order_id == "GGS-999"


@pytest.mark.asyncio
async def test_admin_sends_subscription_data():
    from handlers.admin.orders import sub_send_data, sub_send_start

    async with async_session_maker() as session:
        provider, product, user = await _make_setup(session, Decimal("5"), 9800003)
        order = UnifiedOrder(
            user_id=user.id,
            product_id=product.id,
            api_provider_id=provider.id,
            target="buyer@gmail.com",
            quantity=1,
            price_usd=Decimal("20"),
            cost_price_usd=Decimal("15"),
            status=UnifiedOrderStatus.PENDING,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        order_id = order.id
        telegram_id = user.telegram_id

    bot = FakeBot()
    callback = FakeCallback()
    callback.data = f"admin:sub_send:{order_id}"
    state = FakeState()

    # 1) بدء التسليم اليدوي
    async with async_session_maker() as session:
        await sub_send_start(callback, session, state)
    assert state.state is not None
    prompt = callback.message.sent[-1]["text"]
    assert "تسليم اشتراك رقمي يدوياً" in prompt

    # 2) الأدمن يرسل البيانات
    data_msg = FakeTextMessage("username: acc\npassword: pw\nlink: https://x")

    async with async_session_maker() as session:
        await sub_send_data(data_msg, session, bot, state)

    async with async_session_maker() as session:
        order = await session.get(UnifiedOrder, order_id)
        assert order.status == UnifiedOrderStatus.COMPLETED
        stored = json.loads(order.result_data)
        assert "password: pw" in stored["content"]

    # المستخدم إشعر بالبيانات
    user_msgs = [m for m in bot.sent if m["chat_id"] == telegram_id]
    assert any("وصلك اشتراكك" in m["text"] for m in user_msgs)
    assert any("password: pw" in m["text"] for m in user_msgs)


@pytest.mark.asyncio
async def test_admin_cancels_subscription():
    from handlers.admin.orders import sub_send_data, sub_send_start

    async with async_session_maker() as session:
        provider, product, user = await _make_setup(session, Decimal("5"), 9800004)
        order = UnifiedOrder(
            user_id=user.id,
            product_id=product.id,
            api_provider_id=provider.id,
            target="buyer@gmail.com",
            quantity=1,
            price_usd=Decimal("20"),
            cost_price_usd=Decimal("15"),
            status=UnifiedOrderStatus.PENDING,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        order_id = order.id

    bot = FakeBot()
    callback = FakeCallback()
    callback.data = f"admin:sub_send:{order_id}"
    state = FakeState()

    async with async_session_maker() as session:
        await sub_send_start(callback, session, state)

    cancel_msg = FakeTextMessage("-")

    async with async_session_maker() as session:
        await sub_send_data(cancel_msg, session, bot, state)
        order = await session.get(UnifiedOrder, order_id)
        assert order.status == UnifiedOrderStatus.PENDING  # ما تغيّر
        assert state.cleared is True
