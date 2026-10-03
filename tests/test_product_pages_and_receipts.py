"""تحسينات صفحة المنتج والإيصالات والسلة.

1) صفحة المنتج: سطر «الوقت المتوقع» وسطر المخزون (المتبقي/نفد) — حتى لا
   يدفع الزبون ثم يكتشف أن المنتج نافد.
2) الإيصال: حالة مقروءة بالعربية، بيانات التسليم، وسطر «سيتم إشعارك عند
   اكتمال الخدمة» ما دام الطلب لم يكتمل.
3) السلة: نتيجة موحّدة بنفس هوية تأكيد الطلب.
4) حماية لوحة العروض من التحميل الكسول (تراجع لاسم العرض بدل الانهيار).
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    DigitalInventoryItem,
    InventoryItemStatus,
    Product,
    ProductFulfillmentType,
    NumberOrder,
    ProductStatus,
    SubCategory,
)
from handlers.games import _eta_line, _stock_line
from keyboards.promotions import promotions_kb
from services.order_confirmation_service import OrderConfirmationService as OCS
from services.receipt_service import ReceiptService

PENDING_NOTICE = "سيتم إشعارك عند اكتمال الخدمة"


# ══════════════════ صفحة المنتج ══════════════════


@pytest.mark.asyncio
async def test_eta_line_uses_product_estimated_time():
    product = SimpleNamespace(
        estimated_time="١٠ دقائق",
        requires_link=False,
        requires_quantity=False,
    )
    line = _eta_line(product, "ar")
    assert "الوقت" in line and "١٠ دقائق" in line

    assert _eta_line(SimpleNamespace(estimated_time=None, requires_link=False, requires_quantity=False), "ar") == ""


@pytest.mark.asyncio
async def test_stock_line_shows_remaining_inventory_and_out_of_stock():
    async with async_session_maker() as session:
        cat = Category(name_ar="ألعاب", emoji="🎮", type=CategoryType.GAMES)
        session.add(cat)
        await session.flush()
        sub = SubCategory(category_id=cat.id, name_ar="ببجي")
        session.add(sub)
        await session.flush()
        product = Product(
            sub_category_id=sub.id,
            name_ar="325 UC",
            price_usd=Decimal("5"),
            fulfillment_type=ProductFulfillmentType.INVENTORY,
            status=ProductStatus.ACTIVE,
        )
        session.add(product)
        await session.flush()
        session.add(
            DigitalInventoryItem(
                product_id=product.id,
                encrypted_value="x",
                status=InventoryItemStatus.AVAILABLE,
            )
        )
        await session.commit()
        product_id = product.id

    async with async_session_maker() as session:
        fresh = await session.get(Product, product_id)
        line = await _stock_line(session, fresh, "ar")
    assert "المتبقي" in line and "1" in line

    # منتج عادي (API) لا يظهر له سطر مخزون
    async with async_session_maker() as session:
        api_product = SimpleNamespace(
            id=product_id,
            fulfillment_type=ProductFulfillmentType.API,
        )
        assert await _stock_line(session, api_product, "ar") == ""


@pytest.mark.asyncio
async def test_stock_line_reports_out_of_stock():
    async with async_session_maker() as session:
        cat = Category(name_ar="برامج", emoji="📱", type=CategoryType.APPS)
        session.add(cat)
        await session.flush()
        sub = SubCategory(category_id=cat.id, name_ar="شير")
        session.add(sub)
        await session.flush()
        product = Product(
            sub_category_id=sub.id,
            name_ar="شهر شير",
            price_usd=Decimal("3"),
            fulfillment_type=ProductFulfillmentType.INVENTORY,
        )
        session.add(product)
        await session.commit()
        product_id = product.id

    async with async_session_maker() as session:
        fresh = await session.get(Product, product_id)
        line = await _stock_line(session, fresh, "ar")
    assert "نفد المخزون" in line


# ══════════════════ الإيصال ══════════════════


def _order(**kw):
    base = dict(
        id=4821,
        status=SimpleNamespace(value="pending"),
        price_usd=Decimal("5.00"),
        quantity=1,
        target="5123",
        created_at=datetime(2026, 10, 3, 14, 30),
        result_data=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_receipt_shows_arabic_status_and_pending_notice():
    text = ReceiptService.unified_text(_order(), SimpleNamespace(telegram_id=555), SimpleNamespace(name_ar="325 UC"))
    assert "إيصال" in text
    assert "قيد الانتظار" in text  # حالة مقروءة لا قيمة خام
    assert "completed" not in text
    assert text.rstrip().endswith(f"🔔 <b>{PENDING_NOTICE}</b>")


def test_receipt_shows_delivery_data_and_thanks_when_completed():
    order = _order(
        status=SimpleNamespace(value="completed"),
        result_data=json.dumps({"delivery": {"code": "LUX-1234"}}),
    )
    text = ReceiptService.unified_text(order, SimpleNamespace(telegram_id=555), SimpleNamespace(name_ar="اشتراك"))
    assert "مكتمل" in text
    assert "LUX-1234" in text  # بيانات التسليم محفوظة في الإيصال
    assert "شكراً لاستخدامك المتجر" in text
    assert PENDING_NOTICE not in text


def test_number_receipt_labels_and_notice():
    order = SimpleNamespace(
        id=99,
        status=SimpleNamespace(value="code_received"),
        phone_number="+96170123456",
        service="telegram",
        country_code="le",
        price_sell_usd=Decimal("0.35"),
        purchased_at=datetime(2026, 10, 3, 16, 5),
    )
    text = ReceiptService.number_text(order, SimpleNamespace(telegram_id=555))
    assert "استلام الكود" in text
    assert "+96170123456" in text


# ══════════════════ السلة ══════════════════


def test_cart_summary_uses_unified_style():
    text = OCS.cart(
        completed=3,
        failed=1,
        saved_usd=Decimal("0.75"),
        charged_usd=Decimal("12.00"),
        deliveries=["LUX-1", "LUX-2"],
    )
    assert "السلة" in text
    assert "3" in text and "1" in text
    assert "12.00$" in text and "0.75$" in text
    assert "LUX-1" in text
    assert PENDING_NOTICE in text


# ══════════════════ حماية من التحميل الكسول ══════════════════


def test_promotions_kb_falls_back_when_product_relation_not_loaded():
    class ExplodingPromotion:
        name = "عرض الصيف"
        product_id = 3
        discount_type = SimpleNamespace(value="percent")
        discount_value = Decimal("10")

        @property
        def product(self):
            raise RuntimeError("greenlet_spawn has not been called")

    kb = promotions_kb([ExplodingPromotion()])
    assert kb.inline_keyboard[0][0].text.startswith("🔥 عرض الصيف")


@pytest.mark.asyncio
async def test_cart_checkout_builds_unified_summary():
    """مسار السلة نفسه (لا الباني فقط) ينجح وينتج نصاً موحّداً."""
    from database.models import User

    from handlers.cart import cart_checkout

    class FakeMessage:
        def __init__(self):
            self.edits: list[str] = []
            self.answers: list[str] = []

        async def edit_text(self, text, **kwargs):
            self.edits.append(text)
            return None

        async def answer(self, text, **kwargs):
            self.answers.append(text)
            return None

    class FakeCallback:
        def __init__(self):
            self.message = FakeMessage()

        async def answer(self, *args, **kwargs):
            return None

    class FakeState:
        def __init__(self):
            self.cleared = False

        async def get_data(self):
            return {}

        async def clear(self):
            self.cleared = True

    async with async_session_maker() as session:
        user = User(telegram_id=5600001, username="cart", balance=Decimal("50"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    callback = FakeCallback()
    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await cart_checkout(callback, session, db_user, FakeState())

    text = callback.message.edits[0] if callback.message.edits else ""
    assert "السلة" in text  # لم يرمِ NameError/UnboundLocalError


# ══════════════════ شاشات «طلباتي» ══════════════════


class _FakeMessage:
    def __init__(self):
        self.edits: list[tuple] = []
        self.answers: list[str] = []
        self.markups = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs.get("reply_markup")))
        self.markups.append(kwargs.get("reply_markup"))
        return None

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        return None


class _FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()
        self.alerts: list[str] = []

    async def answer(self, text=None, **kwargs):
        if kwargs.get("show_alert"):
            self.alerts.append(text or "")
        return None


@pytest.mark.asyncio
async def test_empty_orders_screens_are_not_dead_ends():
    """شاشة «لا يوجد طلبات» كانت تُرسل بلا أي زر: رسالة ميتة بلا مخرج."""
    from database.models import User

    from handlers.account import my_number_orders, my_unified_orders

    async with async_session_maker() as session:
        user = User(telegram_id=5600010, username="empty_orders", balance=Decimal("0"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    for handler, data in (
        (my_number_orders, "my_num_orders:0"),
        (my_unified_orders, "my_uni_orders:0"),
    ):
        callback = _FakeCallback(data)
        async with async_session_maker() as session:
            db_user = await session.get(User, user_id)
            await handler(callback, session, db_user)
        markup = callback.message.markups[-1]
        data_all = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert data_all, "الشاشة الفارغة بلا أزرار"
        assert "back_to_main" in data_all


@pytest.mark.asyncio
async def test_number_orders_list_has_receipt_and_repeat_buttons():
    """كل طلب رقم يعرض إيصاله وزر أرقام مشابهة، وبطاقة أوضح."""
    from database.models import OrderStatus, ProviderName, User

    from handlers.account import my_number_orders

    async with async_session_maker() as session:
        user = User(telegram_id=5600011, username="num_orders", balance=Decimal("0"))
        session.add(user)
        await session.flush()
        session.add(
            NumberOrder(
                user_id=user.id,
                provider=ProviderName.FIVESIM,
                provider_order_id="prov-1",
                service="wa",
                country_code="le",
                phone_number="+96170000001",
                price_provider_usd=Decimal("0.10"),
                price_sell_usd=Decimal("0.35"),
                status=OrderStatus.PENDING,
                purchased_at=datetime(2026, 10, 3, 16, 5),
            )
        )
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    callback = _FakeCallback("my_num_orders:0")
    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await my_number_orders(callback, session, db_user)

    text = callback.message.edits[-1][0]
    assert "+96170000001" in text and "📊 الحالة" in text and "0.35" in text
    cbs = [b.callback_data for row in callback.message.markups[-1].inline_keyboard for b in row]
    assert any(c.startswith("receipt:number:") for c in cbs)
    assert "num_svc:wa" in cbs


@pytest.mark.asyncio
async def test_number_order_receipt_handler_renders_receipt():
    """زر الإيصال على طلب الرقم كان غائباً تماماً."""
    from database.models import OrderStatus, ProviderName, User

    from handlers.account import number_order_receipt

    async with async_session_maker() as session:
        user = User(telegram_id=5600012, username="num_receipt", balance=Decimal("0"))
        session.add(user)
        await session.flush()
        order = NumberOrder(
            user_id=user.id,
            provider=ProviderName.FIVESIM,
            provider_order_id="prov-2",
            service="tg",
            country_code="le",
            phone_number="+96170000002",
            price_provider_usd=Decimal("0.10"),
            price_sell_usd=Decimal("0.40"),
            status=OrderStatus.COMPLETED,
            sms_code="99881",
            purchased_at=datetime(2026, 10, 3, 16, 5),
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        order_id, user_id = order.id, user.id

    callback = _FakeCallback(f"receipt:number:{order_id}")
    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await number_order_receipt(callback, session, db_user)
    assert callback.message.answers and "+96170000002" in callback.message.answers[-1]

    # طلب لا يملكه المستخدم → تنبيه فقط
    other = _FakeCallback("receipt:number:999999")
    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await number_order_receipt(other, session, db_user)
    assert other.alerts and not other.message.answers


def test_empty_state_kb_offers_search_and_main_menu():
    from keyboards.common import empty_state_kb
    from keyboards.store import store_empty_section_kb

    kb = empty_state_kb("ar")
    cbs = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert cbs == ["menu:search", "store:home", "back_to_main"]

    kb2 = store_empty_section_kb("ar")
    cbs2 = [b.callback_data for row in kb2.inline_keyboard for b in row]
    assert "menu:search" in cbs2 and "store:home" in cbs2 and "back_to_main" in cbs2


@pytest.mark.asyncio
async def test_flow_cancel_clears_state_and_renders_menu():
    """زر الإلغاء في شاشات الإدخال: ينهي الحالة ويرجع للقائمة (لا رسالة ميتة)."""
    from database.models import User

    from handlers.start import flow_cancel
    from keyboards.common import flow_cancel_kb

    kb = flow_cancel_kb("ar")
    assert kb.inline_keyboard[0][0].callback_data == "flow:cancel"

    class FakeState:
        def __init__(self):
            self.cleared = False

        async def clear(self):
            self.cleared = True

    async with async_session_maker() as session:
        user = User(telegram_id=5600013, username="cancel_flow", balance=Decimal("0"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    class FakeMessage:
        def __init__(self):
            self.edits = []

        async def edit_text(self, text, **kwargs):
            self.edits.append((text, kwargs.get("reply_markup")))
            return None

        async def answer(self, text, **kwargs):
            self.edits.append((text, kwargs.get("reply_markup")))
            return None

    class FakeCallback:
        def __init__(self):
            self.message = FakeMessage()
            self.answered = False

        async def answer(self, *args, **kwargs):
            self.answered = True
            return None

    state = FakeState()
    callback = FakeCallback()
    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await flow_cancel(callback, session, db_user, state)

    assert state.cleared and callback.answered
    assert callback.message.edits and callback.message.edits[-1][1] is not None
