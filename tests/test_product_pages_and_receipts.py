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
