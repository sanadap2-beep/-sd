"""البحث المحسّن + نبضة لوحة الأدمن + تذكير السلة المتروكة.

1) البحث: تطبيع عربي (الهمزات/التاء المربوطة) + تطابق كل الكلمات +
   ترتيب بالصلة + اقتراحات عند عدم وجود نتائج بدل «لا نتائج» يابسة.
2) نبضة الأدمن: أرقام اليوم تظهر أعلى لوحة التحكم، وأي فشل لا يُسقطها.
3) تذكير السلة المتروكة: تذكير واحد لكل سلة، ولا يتكرر إلا إن تغيّرت.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from database.engine import async_session_maker
from database.models import (
    CartItem,
    Category,
    CategoryType,
    Product,
    ProductStatus,
    SubCategory,
    User,
)


async def _seed_products() -> None:
    """منتجات للبحث: ببجي 60، ببجي 325، آيفون (مكتوبة بألف)."""
    async with async_session_maker() as session:
        cat = Category(name_ar="ألعاب", emoji="🎮", type=CategoryType.GAMES)
        session.add(cat)
        await session.flush()
        sub = SubCategory(category_id=cat.id, name_ar="شحن", emoji="🔫")
        session.add(sub)
        await session.flush()
        rows = [
            ("شحن ببجي 60 UC", Decimal("1.00"), 0, False),
            ("شحن ببجي 325 UC", Decimal("4.00"), 3, False),
            ("ايفون — اشتراك شهري", Decimal("9.00"), 0, True),
        ]
        for name, price, sold, featured in rows:
            session.add(
                Product(
                    sub_category_id=sub.id,
                    name_ar=name,
                    price_usd=price,
                    cost_price_usd=Decimal("0.50"),
                    status=ProductStatus.ACTIVE,
                    total_sold=sold,
                    is_featured=featured,
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_search_normalizes_arabic_and_matches_all_tokens():
    """«أيفون» تجد «ايفون»، و«ببجي 60» تجد منتج 60 لا 325."""
    from services.product_service import ProductService

    await _seed_products()
    async with async_session_maker() as session:
        by_hamza = await ProductService.search_products(session, "أيفون", limit=10)
        assert by_hamza and "ايفون" in by_hamza[0].name_ar

        ranked = await ProductService.search_products(session, "ببجي 60", limit=10)
        assert ranked, "البحث متعدد الكلمات لم يجد شيئاً"
        assert "60 UC" in ranked[0].name_ar


@pytest.mark.asyncio
async def test_search_suggests_close_matches_when_nothing_found():
    """خطأ مطبعي بسيط يعطي اقتراحات بدل رسالة يابسة."""
    from services.product_service import ProductService

    await _seed_products()
    async with async_session_maker() as session:
        exact = await ProductService.search_products(session, "ببكجي", limit=10)
        suggestions = await ProductService.suggest_products(session, "ببكجي", limit=5)
    assert exact == []
    assert suggestions, "لا نتائج ولا اقتراحات = زبون يخرج خالي الوفاض"
    assert any("ببجي" in product.name_ar for product in suggestions)


@pytest.mark.asyncio
async def test_admin_pulse_summarizes_today():
    """نبضة اللوحة تعرض أرقام اليوم، ولا تُسقط اللوحة إن تعذّر شيء."""
    from services.admin_pulse_service import AdminPulseService

    async with async_session_maker() as session:
        user = User(telegram_id=5800001, username="pulse", balance=Decimal("5"))
        session.add(user)
        await session.commit()

    async with async_session_maker() as session:
        data = await AdminPulseService.summary(session)
    assert data, "النبضة لم ترجع أرقاماً"
    text = AdminPulseService.render(data)
    assert "نبضة اليوم" in text and "طلبات" in text
    assert AdminPulseService.render({}) == ""


@pytest.mark.asyncio
async def test_cart_reminder_sends_once_per_cart(monkeypatch):
    """تذكير واحد: السلة القديمة تُذكَّر مرة، ثم تسكت حتى تتغيّر."""
    from services.cart_reminder_service import CartReminderService

    sent_messages: list[tuple[int, str]] = []

    class FakeNotifier:
        def __init__(self, bot):
            pass

        async def notify_user(self, telegram_id, text, **kwargs):
            sent_messages.append((telegram_id, text))
            return True

    # الخدمة تستورد NotificationService وقت الاستدعاء → نستبدلها في مصدرها
    import services.notification_service as notification_module

    monkeypatch.setattr(notification_module, "NotificationService", FakeNotifier)

    user_id: int
    async with async_session_maker() as session:
        cat = Category(name_ar="ألعاب", emoji="🎮", type=CategoryType.GAMES)
        session.add(cat)
        await session.flush()
        sub = SubCategory(category_id=cat.id, name_ar="شحن", emoji="🔫")
        session.add(sub)
        await session.flush()
        product = Product(
            sub_category_id=sub.id,
            name_ar="شحن ببجي 60 UC",
            price_usd=Decimal("1.00"),
            cost_price_usd=Decimal("0.50"),
            status=ProductStatus.ACTIVE,
            min_quantity=1,
            max_quantity=10,
        )
        fresh_product = Product(
            sub_category_id=sub.id,
            name_ar="شحن ببجي 325 UC",
            price_usd=Decimal("4.00"),
            cost_price_usd=Decimal("2.00"),
            status=ProductStatus.ACTIVE,
            min_quantity=1,
            max_quantity=10,
        )
        session.add_all([product, fresh_product])
        await session.flush()
        user = User(telegram_id=5800002, username="cart", balance=Decimal("10"))
        session.add(user)
        await session.flush()
        stale = datetime.utcnow() - timedelta(hours=10)
        session.add(
            CartItem(
                user_id=user.id,
                product_id=product.id,
                quantity=2,
                created_at=stale,
                updated_at=stale,
            )
        )
        # سلة حديثة: يجب ألا تُذكَّر
        session.add(
            CartItem(
                user_id=user.id,
                product_id=fresh_product.id,
                quantity=1,
                target="معرّف آخر",
            )
        )
        await session.commit()
        user_id = user.id

    first = await CartReminderService.cycle(bot=object())
    assert first == 1
    assert sent_messages and "ببجي" in sent_messages[0][1]

    # الدورة الثانية: لا تكرار
    second = await CartReminderService.cycle(bot=object())
    assert second == 0
    assert len(sent_messages) == 1

    async with async_session_maker() as session:
        items = (
            await session.execute(
                __import__("sqlalchemy").select(CartItem).where(CartItem.user_id == user_id)
            )
        ).scalars().all()
        assert any(item.reminder_sent_at is not None for item in items)
