"""Tests for manual products: purchase → channel alert → accept/reject."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
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


class FakeBot:
    def __init__(self):
        self.sent = []
        self._id = 1000

    async def send_message(self, chat_id=None, text=None, **kwargs):
        self._id += 1
        msg = type("M", (), {"message_id": self._id})()
        self.sent.append(
            {"chat_id": chat_id, "text": text, "reply_markup": kwargs.get("reply_markup")}
        )
        return msg

    async def edit_message_text(self, chat_id=None, message_id=None, text=None, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, "edited": True})
        return None


class FakeMessage:
    def __init__(self):
        self.sent = []

    async def answer(self, text, **kwargs):
        self.sent.append({"text": text, "reply_markup": kwargs.get("reply_markup")})

    async def edit_text(self, text, **kwargs):
        self.sent.append({"text": text, "edited": True, "reply_markup": kwargs.get("reply_markup")})


class FakeCallback:
    def __init__(self):
        self.message = FakeMessage()
        self.answers = []
        self.data = ""

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)


class FakeState:
    def __init__(self, data):
        self._data = data
        self.cleared = False

    async def get_data(self):
        return dict(self._data)

    async def clear(self):
        self.cleared = True


async def _make_manual_product(session) -> Product:
    category = Category(
        name_ar="يدوي", emoji="🖐", type=CategoryType.CUSTOM, is_active=True
    )
    session.add(category)
    await session.flush()
    sub = SubCategory(
        category_id=category.id, name_ar="خدمات يدوية", emoji="🖐", is_active=True
    )
    session.add(sub)
    await session.flush()
    product = Product(
        sub_category_id=sub.id,
        name_ar="تفعيل حساب يدوي",
        price_usd=Decimal("10"),
        cost_price_usd=Decimal("4"),
        pricing_type=ProductPricingType.FIXED,
        fulfillment_type=ProductFulfillmentType.MANUAL,
        min_quantity=1,
        max_quantity=1,
        requires_link=True,
        requires_quantity=False,
        display_type=ProductDisplayType.FIXED_TOTAL,
        status=ProductStatus.ACTIVE,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def _make_user(session, telegram_id: int, balance="50") -> User:
    user = User(
        telegram_id=telegram_id,
        username=f"buyer_{telegram_id}",
        full_name="Buyer",
        balance=Decimal(balance),
        is_activated=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_manual_purchase_notifies_user_and_channel():
    from handlers.games import _execute_purchase

    async with async_session_maker() as session:
        product = await _make_manual_product(session)
        user = await _make_user(session, 9900001)
        product_id = product.id
        user_id = user.id
        telegram_id = user.telegram_id

    bot = FakeBot()
    callback = FakeCallback()
    state = FakeState({"target": "https://t.me/someaccount", "quantity": 1})

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        await _execute_purchase(callback, session, user, bot, state, product_id, None)

    # المستخدم راح له إشعار واضح أن المنتج يدوي
    user_texts = [m["text"] for m in callback.message.sent if m.get("text")]
    assert any("منتج يدوي" in t for t in user_texts)
    assert any("بانتظار تنفيذ الإدارة" in t for t in user_texts)

    # الرصيد انقص (10$)
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        assert user.balance == Decimal("40")

        # الطلب PENDING
        order = (
            await session.execute(select(UnifiedOrder).where(UnifiedOrder.user_id == user_id))
        ).scalars().first()
        assert order is not None
        assert order.status == UnifiedOrderStatus.PENDING
        order_id = order.id

    # قناة الأدمن: تنبيه بمعلومات المستخدم + أزرار قبول/رفض
    admin_msgs = [m for m in bot.sent if m["chat_id"] == -1001]
    manual_alert = [m for m in admin_msgs if "طلب يدوي جديد" in m["text"]]
    assert manual_alert, "لا يوجد تنبيه طلب يدوي في قناة الأدمن"
    alert = manual_alert[0]
    assert str(telegram_id) in alert["text"]
    kb = alert["reply_markup"]
    buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"admin:order_complete:{order_id}" in buttons
    assert f"admin:order_refund_ask:{order_id}" in buttons


@pytest.mark.asyncio
async def test_admin_accepts_manual_order_user_notified():
    from handlers.admin.orders import order_complete

    async with async_session_maker() as session:
        product = await _make_manual_product(session)
        user = await _make_user(session, 9900002)
        order = UnifiedOrder(
            user_id=user.id,
            product_id=product.id,
            target="account_xyz",
            quantity=1,
            price_usd=Decimal("10"),
            cost_price_usd=Decimal("4"),
            status=UnifiedOrderStatus.PENDING,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        order_id = order.id
        telegram_id = user.telegram_id

    bot = FakeBot()
    callback = FakeCallback()
    callback.data = f"admin:order_complete:{order_id}"

    async with async_session_maker() as session:
        await order_complete(callback, session, bot)

    async with async_session_maker() as session:
        order = await session.get(UnifiedOrder, order_id)
        assert order.status == UnifiedOrderStatus.COMPLETED

    # المستخدم إشعر أنه تم التنفيذ
    user_msgs = [m for m in bot.sent if m["chat_id"] == telegram_id]
    assert any("تم تنفيذ طلبك" in m["text"] for m in user_msgs)


@pytest.mark.asyncio
async def test_admin_rejects_manual_order_refund_and_notify():
    from handlers.admin.orders import order_refund, order_refund_ask

    async with async_session_maker() as session:
        product = await _make_manual_product(session)
        user = await _make_user(session, 9900003, balance="40")
        order = UnifiedOrder(
            user_id=user.id,
            product_id=product.id,
            target="account_xyz",
            quantity=1,
            price_usd=Decimal("10"),
            cost_price_usd=Decimal("4"),
            status=UnifiedOrderStatus.PENDING,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        order_id = order.id
        user_id = user.id
        telegram_id = user.telegram_id

    bot = FakeBot()
    callback = FakeCallback()

    # 1) سؤال التأكيد
    callback.data = f"admin:order_refund_ask:{order_id}"
    async with async_session_maker() as session:
        await order_refund_ask(callback, session)
    confirm_msg = callback.message.sent[-1]
    kb = confirm_msg["reply_markup"]
    confirm_btn = [b for row in kb.inline_keyboard for b in row][0]
    assert confirm_btn.callback_data == f"admin:order_refund:{order_id}"

    # 2) تأكيد الاسترجاع
    callback.data = f"admin:order_refund:{order_id}"
    async with async_session_maker() as session:
        await order_refund(callback, session, bot)

    async with async_session_maker() as session:
        order = await session.get(UnifiedOrder, order_id)
        assert order.status == UnifiedOrderStatus.REFUNDED
        user = await session.get(User, user_id)
        assert user.balance == Decimal("50")  # رجع 10$

    user_msgs = [m for m in bot.sent if m["chat_id"] == telegram_id]
    assert any("تم استرجاع قيمة طلبك" in m["text"] for m in user_msgs)
