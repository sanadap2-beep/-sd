"""شاشات الطلبات: لا شاشة ميتة، وكل طلب معلّق له طريق للتحديث.

1) قائمة طلبات الأرقام: بطاقة أوضح + زر «تحديث الكود» للطلب المعلّق.
2) قائمة طلبات المتجر: صفّ لكل طلب (تفاصيل + إعادة) بدل تخطيط مكسور.
3) زر «تحديث الحالة» على تفاصيل الطلب: يفحص المزوّد، يحدّث الحالة،
   ويرسل الإيصال عند الاكتمال — هذا ما يجعل وعد «سيتم إشعارك» حقيقياً.
4) «إعادة آخر طلب» من شاشة حسابي بضغطة واحدة.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from database.engine import async_session_maker
from database.models import (
    ApiProvider,
    ApiProviderType,
    Category,
    CategoryType,
    NumberOrder,
    OrderStatus,
    Product,
    ProductStatus,
    ProviderName,
    SubCategory,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)


class _FakeMessage:
    def __init__(self):
        self.edits: list[tuple] = []
        self.answers: list[str] = []

    async def edit_text(self, text, **kwargs):
        self.edits.append((text, kwargs.get("reply_markup")))
        return None

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        return None


class _FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()
        self.alerts: list[str] = []
        self.answers: list[str] = []

    async def answer(self, text=None, **kwargs):
        if kwargs.get("show_alert"):
            self.alerts.append(text or "")
        else:
            self.answers.append(text or "")
        return None


class _FakeState:
    def __init__(self):
        self.cleared = False
        self.data: dict = {}

    async def clear(self):
        self.cleared = True

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)


class _FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))
        return None


def _buttons(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def _make_user(telegram_id: int, username: str) -> int:
    async with async_session_maker() as session:
        user = User(telegram_id=telegram_id, username=username, balance=Decimal("20"))
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id


@pytest.mark.asyncio
async def test_pending_number_order_gets_refresh_button():
    """الطلب المعلّق يعرض «تحديث الكود» — الطريق الأقصر للكود."""
    from handlers.account import my_number_orders

    user_id = await _make_user(5700001, "num_pending")
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        session.add(
            NumberOrder(
                user_id=user.id,
                provider=ProviderName.FIVESIM,
                provider_order_id="p-1",
                service="wa",
                country_code="le",
                phone_number="+96170000011",
                price_provider_usd=Decimal("0.10"),
                price_sell_usd=Decimal("0.35"),
                status=OrderStatus.PENDING,
                purchased_at=datetime(2026, 10, 3, 16, 5),
            )
        )
        await session.commit()

    callback = _FakeCallback("my_num_orders:0")
    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await my_number_orders(callback, session, db_user)

    markup = callback.message.edits[-1][1]
    buttons = _buttons(markup)
    assert any(c.startswith("num_refresh:") for c in buttons)
    assert any(c.startswith("receipt:number:") for c in buttons)
    assert "menu:account" in buttons


@pytest.mark.asyncio
async def test_unified_orders_list_gives_each_order_its_own_row():
    """لكل طلب صفّه (تفاصيل + إعادة) — التخطيط القديم كان يدمج الطلبات."""
    from handlers.account import my_unified_orders

    user_id = await _make_user(5700002, "uni_rows")
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        for i in range(3):
            session.add(
                UnifiedOrder(
                    user_id=user.id,
                    price_usd=Decimal("1.00"),
                    quantity=1,
                    status=UnifiedOrderStatus.COMPLETED,
                    created_at=datetime(2026, 10, 3, 16, 5),
                    target=f"target-{i}",
                )
            )
        await session.commit()

    callback = _FakeCallback("my_uni_orders:0")
    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await my_unified_orders(callback, session, db_user)

    markup = callback.message.edits[-1][1]
    rows = markup.inline_keyboard
    # 3 صفوف طلبات (زرّان في كل صف) + صف رجوع
    assert [len(r) for r in rows[:3]] == [2, 2, 2]
    buttons = _buttons(markup)
    assert buttons.count("menu:account") == 1
    assert sum(1 for c in buttons if c.startswith("repeat_order:")) == 3


@pytest.mark.asyncio
async def test_order_refresh_polls_provider_and_notifies_on_completion(monkeypatch):
    """زر تحديث الحالة: يفحص المزوّد ويكمل الطلب ويرسل الإيصال."""
    from protocols.factory import ProtocolFactory

    from handlers.account import refresh_unified_order

    user_id = await _make_user(5700003, "refresh_me")
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        provider = ApiProvider(
            name="مزود وهمي",
            type=ApiProviderType.GAMES,
            api_url="https://example.test/api",
            api_key="k",
            is_active=True,
        )
        session.add(provider)
        await session.flush()
        order = UnifiedOrder(
            user_id=user.id,
            api_provider_id=provider.id,
            external_order_id="ext-1",
            price_usd=Decimal("2.00"),
            quantity=1,
            status=UnifiedOrderStatus.PROCESSING,
            created_at=datetime(2026, 10, 3, 16, 5),
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        order_id = order.id

    class FakeProtocol:
        async def check_order_status(self, external_id):
            from protocols.base import ProtocolOrderStatus

            return ProtocolOrderStatus(
                external_order_id=external_id,
                status="completed",
                remains=0,
                start_count=0,
            )

    monkeypatch.setattr(
        ProtocolFactory, "create_from_provider", staticmethod(lambda provider: FakeProtocol())
    )

    callback = _FakeCallback(f"order_refresh:{order_id}")
    bot = _FakeBot()
    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await refresh_unified_order(callback, session, db_user, bot)

    async with async_session_maker() as session:
        order = await session.get(UnifiedOrder, order_id)
        assert order.status == UnifiedOrderStatus.COMPLETED
        assert order.completed_at is not None

    assert bot.sent and "مكتمل" in bot.sent[-1][1]
    assert callback.alerts


@pytest.mark.asyncio
async def test_order_refresh_reports_provider_failure_instead_of_crashing(monkeypatch):
    """المزوّد المتعذر لا يرمي استثناءً للمستخدم."""
    from protocols.factory import ProtocolFactory

    from handlers.account import refresh_unified_order

    user_id = await _make_user(5700004, "refresh_fail")
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        provider = ApiProvider(
            name="مزود متعذر",
            type=ApiProviderType.GAMES,
            api_url="https://example.test/api",
            api_key="k",
            is_active=True,
        )
        session.add(provider)
        await session.flush()
        order = UnifiedOrder(
            user_id=user.id,
            api_provider_id=provider.id,
            external_order_id="ext-2",
            price_usd=Decimal("2.00"),
            quantity=1,
            status=UnifiedOrderStatus.PENDING,
            created_at=datetime(2026, 10, 3, 16, 5),
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        order_id = order.id

    class BrokenProtocol:
        async def check_order_status(self, external_id):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        ProtocolFactory, "create_from_provider", staticmethod(lambda provider: BrokenProtocol())
    )

    callback = _FakeCallback(f"order_refresh:{order_id}")
    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await refresh_unified_order(callback, session, db_user, _FakeBot())

    assert callback.alerts and "تعذّر" in callback.alerts[-1]
    async with async_session_maker() as session:
        order = await session.get(UnifiedOrder, order_id)
        assert order.status == UnifiedOrderStatus.PENDING


@pytest.mark.asyncio
async def test_repeat_last_order_reopens_the_product():
    """«إعادة آخر طلب» من حسابي يعيد فتح شاشة تأكيد المنتج."""
    from handlers.account import repeat_last_order

    user_id = await _make_user(5700005, "repeat_last")
    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        cat = Category(name_ar="ألعاب", emoji="🎮", type=CategoryType.GAMES)
        session.add(cat)
        await session.flush()
        sub = SubCategory(category_id=cat.id, name_ar="شحن ببجي", emoji="🔫")
        session.add(sub)
        await session.flush()
        product = Product(
            sub_category_id=sub.id,
            name_ar="شحن 60 UC",
            price_usd=Decimal("1.00"),
            cost_price_usd=Decimal("0.50"),
            status=ProductStatus.ACTIVE,
        )
        session.add(product)
        await session.flush()
        session.add(
            UnifiedOrder(
                user_id=user.id,
                product_id=product.id,
                price_usd=Decimal("1.00"),
                quantity=1,
                status=UnifiedOrderStatus.COMPLETED,
                created_at=datetime(2026, 10, 3, 16, 5),
                target="5123456789",
            )
        )
        await session.commit()
        await session.refresh(product)
        product_id = product.id

    callback = _FakeCallback("repeat_last")
    state = _FakeState()
    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await repeat_last_order(callback, session, db_user, state)

    assert state.data.get("product_id") == product_id
    assert callback.message.edits and "شحن 60 UC" in callback.message.edits[-1][0]


@pytest.mark.asyncio
async def test_repeat_last_without_orders_alerts_only():
    from handlers.account import repeat_last_order

    user_id = await _make_user(5700006, "repeat_none")
    callback = _FakeCallback("repeat_last")
    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        await repeat_last_order(callback, session, db_user, _FakeState())

    assert callback.alerts and not callback.message.edits


@pytest.mark.asyncio
async def test_main_menu_header_shows_pending_orders_hint():
    """الشاشة الأولى تذكّر الزبون بطلب لم يكتمل (ولا سطر إضافي إن لم يوجد)."""
    from handlers.start import _main_header

    user_id = await _make_user(5700007, "pending_hint")

    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        clean = await _main_header(session, db_user)
    assert "قيد التنفيذ" not in clean

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        session.add(
            UnifiedOrder(
                user_id=user.id,
                price_usd=Decimal("1.00"),
                quantity=1,
                status=UnifiedOrderStatus.PROCESSING,
                created_at=datetime(2026, 10, 3, 16, 5),
            )
        )
        session.add(
            NumberOrder(
                user_id=user.id,
                provider=ProviderName.FIVESIM,
                provider_order_id="p-9",
                service="wa",
                country_code="le",
                phone_number="+96170000099",
                price_provider_usd=Decimal("0.10"),
                price_sell_usd=Decimal("0.35"),
                status=OrderStatus.PENDING,
                purchased_at=datetime(2026, 10, 3, 16, 5),
            )
        )
        await session.commit()

    async with async_session_maker() as session:
        db_user = await session.get(User, user_id)
        with_hint = await _main_header(session, db_user)
    assert "قيد التنفيذ" in with_hint and "2" in with_hint
