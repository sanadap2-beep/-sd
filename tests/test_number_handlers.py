from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import Country, NumberOrder, NumberService, OrderStatus, ProviderName, User
from handlers.numbers import confirm_buy, refresh_order
from providers.base import OrderStatusResult
from providers.manager import ProviderUnavailableError


class DummyMessage:
    def __init__(self):
        self.answers: list[tuple[str, object]] = []
        self.edits: list[tuple[str, object]] = []
        self.chat = SimpleNamespace(id=12345)
        self.message_id = 777

    async def answer(self, text: str, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return SimpleNamespace(chat=self.chat, message_id=len(self.answers) + 100)

    async def edit_text(self, text: str, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))
        return None

    async def edit_reply_markup(self, reply_markup=None):
        return None


class DummyCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = DummyMessage()
        self.answers: list[tuple[str | None, bool]] = []
        self.from_user = SimpleNamespace(id=555, username="tester")

    async def answer(self, text: str | None = None, show_alert: bool = False, **kwargs):
        self.answers.append((text, show_alert))
        return None


class DummyBot:
    async def edit_message_text(self, **kwargs):
        return None

    async def send_document(self, **kwargs):
        return None

    async def send_message(self, *args, **kwargs):
        return None


@dataclass
class FakeBuyResult:
    provider: ProviderName = ProviderName.FIVESIM
    provider_order_id: str = "provider-order-1"
    phone_number: str = "+15550000001"
    cost_usd: Decimal = Decimal("1.00")


async def _seed_number_catalog(balance: str = "10"):
    async with async_session_maker() as session:
        user = User(telegram_id=70001, username="buyer", balance=Decimal(balance))
        service = NumberService(
            code="tg_test",
            name_ar="تيليجرام اختبار",
            emoji="✈️",
            fivesim_code="tg",
            is_active=True,
        )
        country = Country(
            code="zz",
            name_ar="دولة اختبار",
            flag="🏳️",
            fivesim_code="zz",
            is_active=True,
        )
        session.add_all([user, service, country])
        await session.commit()
        await session.refresh(user)
        await session.refresh(service)
        await session.refresh(country)
        return user.id, service.code, country.code


async def _noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_number_confirm_success_creates_pending_order_and_deducts_balance(monkeypatch):
    user_id, service_code, country_code = await _seed_number_catalog("10")

    async def fake_prices(service, country, session=None):
        return {ProviderName.FIVESIM: Decimal("1.00")}

    async def fake_buy(service, country, session=None, preferred_provider=None):
        return FakeBuyResult()

    monkeypatch.setattr("handlers.numbers.provider_manager.get_cheapest_price", fake_prices)
    monkeypatch.setattr("handlers.numbers.provider_manager.buy_number", fake_buy)
    monkeypatch.setattr("services.notification_service.NotificationService.notify_admin", _noop)

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        callback = DummyCallback(f"num_confirm:{service_code}:{country_code}:")
        await confirm_buy(callback, session, user, DummyBot())

        orders = (await session.execute(select(NumberOrder))).scalars().all()
        await session.refresh(user)

    assert len(orders) == 1
    assert orders[0].status == OrderStatus.PENDING
    assert orders[0].phone_number == "+15550000001"
    assert user.balance == Decimal("8.5000")  # 1$ + default 50% margin
    assert callback.message.answers and "تم شراء الرقم" in callback.message.answers[0][0]


@pytest.mark.asyncio
async def test_number_confirm_provider_failure_refunds_full_amount(monkeypatch):
    user_id, service_code, country_code = await _seed_number_catalog("10")

    async def fake_prices(service, country, session=None):
        return {ProviderName.FIVESIM: Decimal("1.00")}

    async def fake_buy(service, country, session=None, preferred_provider=None):
        raise ProviderUnavailableError("empty")

    monkeypatch.setattr("handlers.numbers.provider_manager.get_cheapest_price", fake_prices)
    monkeypatch.setattr("handlers.numbers.provider_manager.buy_number", fake_buy)

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        callback = DummyCallback(f"num_confirm:{service_code}:{country_code}:")
        await confirm_buy(callback, session, user, DummyBot())
        orders = (await session.execute(select(NumberOrder))).scalars().all()
        await session.refresh(user)

    assert orders == []
    assert user.balance == Decimal("10.0000")
    assert any("تم استرجاع رصيدك" in text for text, _markup in callback.message.answers)


@pytest.mark.asyncio
async def test_number_confirm_insufficient_balance_does_not_buy(monkeypatch):
    user_id, service_code, country_code = await _seed_number_catalog("1")
    called = {"buy": False, "insufficient": False}

    async def fake_prices(service, country, session=None):
        return {ProviderName.FIVESIM: Decimal("1.00")}

    async def fake_buy(*args, **kwargs):
        called["buy"] = True
        return FakeBuyResult()

    async def fake_insufficient(self, *args, **kwargs):
        called["insufficient"] = True
        return True

    monkeypatch.setattr("handlers.numbers.provider_manager.get_cheapest_price", fake_prices)
    monkeypatch.setattr("handlers.numbers.provider_manager.buy_number", fake_buy)
    monkeypatch.setattr(
        "services.notification_service.NotificationService.notify_insufficient_balance",
        fake_insufficient,
    )

    async with async_session_maker() as session:
        user = await session.get(User, user_id)
        callback = DummyCallback(f"num_confirm:{service_code}:{country_code}:")
        await confirm_buy(callback, session, user, DummyBot())
        orders = (await session.execute(select(NumberOrder))).scalars().all()
        await session.refresh(user)

    assert called["insufficient"] is True
    assert called["buy"] is False
    assert orders == []
    assert user.balance == Decimal("1.0000")


@pytest.mark.asyncio
async def test_number_confirm_active_order_limit_blocks_extra_purchase(monkeypatch):
    user_id, service_code, country_code = await _seed_number_catalog("10")
    called = {"prices": False}

    async def fake_prices(*args, **kwargs):
        called["prices"] = True
        return {ProviderName.FIVESIM: Decimal("1.00")}

    monkeypatch.setattr("handlers.numbers.provider_manager.get_cheapest_price", fake_prices)

    async with async_session_maker() as session:
        for index in range(3):
            session.add(
                NumberOrder(
                    user_id=user_id,
                    provider=ProviderName.FIVESIM,
                    provider_order_id=f"active-{index}",
                    service=service_code,
                    country_code=country_code,
                    phone_number=f"+1000{index}",
                    price_provider_usd=Decimal("1"),
                    price_sell_usd=Decimal("1.5"),
                    status=OrderStatus.PENDING,
                    expires_at=datetime.utcnow() + timedelta(minutes=5),
                )
            )
        await session.commit()
        user = await session.get(User, user_id)
        callback = DummyCallback(f"num_confirm:{service_code}:{country_code}:")
        await confirm_buy(callback, session, user, DummyBot())

    assert called["prices"] is False
    assert callback.answers[-1][1] is True
    assert "طلبات نشطة" in callback.answers[-1][0]


@pytest.mark.asyncio
async def test_number_refresh_delivers_received_code(monkeypatch):
    user_id, service_code, country_code = await _seed_number_catalog("10")

    async def fake_check(provider, external_order_id):
        return OrderStatusResult("code_received", "123456", "Your code is 123456", {})

    async def fake_finish(provider, order_id):
        return True

    monkeypatch.setattr("services.sms_receiver_service.SMSReceiverService.check", fake_check)
    monkeypatch.setattr("tasks.order_monitor.provider_manager.finish_order", fake_finish)
    monkeypatch.setattr("services.notification_service.NotificationService.notify_user", _noop)
    monkeypatch.setattr("services.notification_service.NotificationService.notify_successful_number_order", _noop)

    async with async_session_maker() as session:
        order = NumberOrder(
            user_id=user_id,
            provider=ProviderName.FIVESIM,
            provider_order_id="status-1",
            service=service_code,
            country_code=country_code,
            phone_number="+15550009999",
            price_provider_usd=Decimal("1"),
            price_sell_usd=Decimal("1.5"),
            status=OrderStatus.PENDING,
            expires_at=datetime.utcnow() + timedelta(minutes=5),
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        user = await session.get(User, user_id)
        callback = DummyCallback(f"num_refresh:{order.id}")
        await refresh_order(callback, session, user, DummyBot())
        await session.refresh(order)

    assert order.status == OrderStatus.COMPLETED
    assert order.sms_code == "123456"
    assert order.full_sms_text == "Your code is 123456"
    assert order.completed_at is not None
