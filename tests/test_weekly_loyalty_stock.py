"""تقرير الأسبوع + سلسلة الولاء + حارس المخزون.

1) التقرير الأسبوعي: رسالة واحدة فيها الطلبات والإيراد والربح
   والمستخدمون الجدد والأكثر مبيعاً، ولا تُسقط اللوحة إن تعذّر شيء.
2) سلسلة الولاء: ٣ طلبات في الشهر ⇒ كوبون ٥٪، ولا يتكرر لنفس العتبة.
3) حارس المخزون: منتج نافد يُخفى تلقائياً ويُعاد متى عاد مخزونه،
   بدل أن يشتري الزبون ويفشل التسليم.
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
    DigitalInventoryItem,
    InventoryItemStatus,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    SubCategory,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)


async def _seed_store():
    """فئة + منتجان (أحدهما مخزون رقمي)."""
    async with async_session_maker() as session:
        cat = Category(name_ar="ألعاب", emoji="🎮", type=CategoryType.GAMES)
        session.add(cat)
        await session.flush()
        sub = SubCategory(category_id=cat.id, name_ar="شحن", emoji="🔫")
        session.add(sub)
        await session.flush()
        inventory_product = Product(
            sub_category_id=sub.id,
            name_ar="كود جاهز ببجي",
            price_usd=Decimal("5.00"),
            cost_price_usd=Decimal("2.00"),
            status=ProductStatus.ACTIVE,
            fulfillment_type=ProductFulfillmentType.INVENTORY,
        )
        normal_product = Product(
            sub_category_id=sub.id,
            name_ar="شحن فوري",
            price_usd=Decimal("3.00"),
            cost_price_usd=Decimal("1.00"),
            status=ProductStatus.ACTIVE,
        )
        session.add_all([inventory_product, normal_product])
        await session.commit()
        return int(inventory_product.id), int(normal_product.id)


class _FakeNotifier:
    """بديل إشعارات يجمع ما أُرسل بدل إرساله فعلاً."""

    admin_messages: list[str] = []
    user_messages: list[str] = []

    def __init__(self, bot):
        pass

    async def notify_admin(self, text, **kwargs):
        _FakeNotifier.admin_messages.append(text)
        return True

    async def notify_user(self, telegram_id, text, **kwargs):
        _FakeNotifier.user_messages.append(text)
        return True


@pytest.mark.asyncio
async def test_weekly_report_summarizes_the_week():
    """التقرير يعرض أرقام الأسبوع واتجاهها مقارنة بما قبله."""
    from services.weekly_admin_report_service import WeeklyAdminReportService

    _, normal_id = await _seed_store()
    async with async_session_maker() as session:
        user = User(telegram_id=6000001, username="weekly", balance=Decimal("10"))
        session.add(user)
        await session.flush()
        now = datetime.utcnow()
        for index in range(3):
            session.add(
                UnifiedOrder(
                    user_id=user.id,
                    product_id=normal_id,
                    price_usd=Decimal("10.00"),
                    cost_price_usd=Decimal("4.00"),
                    status=UnifiedOrderStatus.COMPLETED,
                    created_at=now - timedelta(days=index),
                )
            )
        await session.commit()

    async with async_session_maker() as session:
        text = await WeeklyAdminReportService.build(session)

    assert "تقرير الأسبوع" in text
    assert "٣" in text or "3" in text, f"عدد الطلبات غائب عن التقرير:\n{text}"
    assert "30.00$" in text, f"الإيراد غائب:\n{text}"
    assert "18.00$" in text, f"الربح التقريبي غائب:\n{text}"
    assert "الأكثر مبيعاً" in text, f"قسم الأكثر مبيعاً غائب:\n{text}"
    assert "شحن فوري" in text, f"اسم المنتج الأكثر مبيعاً غائب:\n{text}"
    assert WeeklyAdminReportService.render({}) == ""


@pytest.mark.asyncio
async def test_loyalty_chain_grants_coupon_once_per_tier(monkeypatch):
    """٣ طلبات ⇒ كوبون ٥٪ واحد فقط، ولا يتكرر في الدورة التالية."""
    from services.loyalty_chain_service import LoyaltyChainService

    _FakeNotifier.admin_messages.clear()
    _FakeNotifier.user_messages.clear()
    import services.notification_service as notification_module

    monkeypatch.setattr(notification_module, "NotificationService", _FakeNotifier)

    async with async_session_maker() as session:
        user = User(telegram_id=6000002, username="loyal", balance=Decimal("10"))
        session.add(user)
        await session.flush()
        now = datetime.utcnow()
        for _ in range(3):
            session.add(
                UnifiedOrder(
                    user_id=user.id,
                    price_usd=Decimal("2.00"),
                    status=UnifiedOrderStatus.COMPLETED,
                    created_at=now,
                )
            )
        await session.commit()

    first = await LoyaltyChainService.grant(bot=object())
    assert first == 1, "ثلاثة طلبات في الشهر يجب أن تمنح كوبوناً واحداً"
    assert len(_FakeNotifier.user_messages) == 1
    assert "LOY" in _FakeNotifier.user_messages[0]
    assert "٥٪" in _FakeNotifier.user_messages[0] or "5%" in _FakeNotifier.user_messages[0]

    second = await LoyaltyChainService.grant(bot=object())
    assert second == 0, "الكوبون تكرر لنفس العتبة في نفس الشهر"
    assert len(_FakeNotifier.user_messages) == 1

    async with async_session_maker() as session:
        user = (
            await session.execute(
                select(User).where(User.telegram_id == 6000002)
            )
        ).scalar_one()
        assert (user.loyalty_points or 0) > 0, "يجب أن تُمنح نقاط ولاء مع الكوبون"


@pytest.mark.asyncio
async def test_stock_guard_hides_empty_product_and_restores_it(monkeypatch):
    """منتج نافد يُخفى تلقائياً، ثم يعود تلقائياً متى توفر المخزون."""
    from services.inventory_guard_service import InventoryGuardService

    _FakeNotifier.admin_messages.clear()
    _FakeNotifier.user_messages.clear()
    import services.notification_service as notification_module

    monkeypatch.setattr(notification_module, "NotificationService", _FakeNotifier)

    product_id, _ = await _seed_store()

    # لا عناصر على الإطلاق ⇒ نفاد
    result = await InventoryGuardService.cycle(bot=object())
    assert result.get("hidden") == 1, "المنتج النافد يجب أن يُخفى"
    assert _FakeNotifier.admin_messages
    assert "نفد المخزون" in _FakeNotifier.admin_messages[-1]

    async with async_session_maker() as session:
        product = await session.get(Product, product_id)
        assert product.status == ProductStatus.INACTIVE

    # زوّدنا المخزون ⇒ يعود تلقائياً
    async with async_session_maker() as session:
        session.add(
            DigitalInventoryItem(
                product_id=product_id,
                encrypted_value="dummy-encrypted",
                status=InventoryItemStatus.AVAILABLE,
            )
        )
        await session.commit()

    result = await InventoryGuardService.cycle(bot=object())
    assert result.get("restored") == 1, "المتوفر يجب أن يعود للمتجر"
    assert "عاد المنتج للمتجر" in _FakeNotifier.admin_messages[-1]

    async with async_session_maker() as session:
        product = await session.get(Product, product_id)
        assert product.status == ProductStatus.ACTIVE
