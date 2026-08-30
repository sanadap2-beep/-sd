from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from api.app import _save_local_token
from database.engine import async_session_maker
from database.models import (
    Country,
    NumberOrder,
    NumberService,
    OrderStatus,
    ProviderName,
    ProviderStatus,
    User,
)
from providers.base import PurchasedNumber
from providers.manager import ProviderManager
from services.bulk_number_service import BulkNumberService


@dataclass
class _FakeBuyResult:
    provider: ProviderName
    provider_order_id: str
    phone_number: str
    cost_usd: Decimal


@pytest.mark.asyncio
async def test_setup_token_writer_uses_real_newlines(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "local.env").write_text("BOT_USERNAME=test_bot\n", encoding="utf-8")

    _save_local_token("123456:TEST_TOKEN_FOR_TESTS_1234567890")

    content = (tmp_path / "local.env").read_text(encoding="utf-8")
    assert content.splitlines() == [
        "BOT_TOKEN=123456:TEST_TOKEN_FOR_TESTS_1234567890",
        "BOT_USERNAME=test_bot",
    ]
    assert "\\n" not in content


@pytest.mark.asyncio
async def test_bulk_refund_uses_discounted_unit_price_and_no_shared_session(monkeypatch):
    async with async_session_maker() as session:
        user = User(telegram_id=9001, balance=Decimal("100"))
        service = NumberService(code="tg", name_ar="Telegram", fivesim_code="telegram")
        country = Country(
            code="tr",
            name_ar="تركيا",
            fivesim_code="turkey",
            is_active=True,
        )
        session.add_all([user, service, country])
        await session.commit()
        await session.refresh(user)

        async def fake_quote(_session, _service, _country, quantity):
            return {
                "quantity": quantity,
                "unit_price_usd": Decimal("10"),
                "gross_usd": Decimal("40"),
                "discount_percent": Decimal("10"),
                "discount_usd": Decimal("4"),
                "total_usd": Decimal("36"),
            }

        calls = []

        async def fake_buy_number(_service, _country, session_arg=None, preferred_provider=None):
            calls.append(session_arg)
            index = len(calls)
            if index == 2:
                raise RuntimeError("provider temporarily empty")
            return _FakeBuyResult(
                provider=ProviderName.FIVESIM,
                provider_order_id=f"p-{index}",
                phone_number=f"+90000{index}",
                cost_usd=Decimal("5"),
            )

        monkeypatch.setattr(BulkNumberService, "quote", staticmethod(fake_quote))
        monkeypatch.setattr(
            "services.bulk_number_service.provider_manager.buy_number",
            fake_buy_number,
        )
        async def fake_track(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "services.bulk_number_service.FeatureService.track",
            staticmethod(fake_track),
        )

        result = await BulkNumberService.execute(
            session, user.id, service, country, quantity=4
        )

        assert calls == [None, None, None, None]
        assert result["succeeded"] == 3
        assert result["failed"] == 1
        assert result["refunded_usd"] == Decimal("9.0000")
        assert result["net_charged_usd"] == Decimal("27.0000")

        await session.refresh(user)
        assert user.balance == Decimal("73.0000")

        orders = (
            await session.execute(
                select(NumberOrder).where(NumberOrder.user_id == user.id)
            )
        ).scalars().all()
        assert len(orders) == 3
        assert {order.price_sell_usd for order in orders} == {Decimal("9.0000")}


class _FakeNumberProvider:
    def __init__(self, provider: ProviderName, price: Decimal):
        self.provider = provider
        self.price = price
        self.bought = 0

    async def get_price(self, country: str, service: str):
        return self.price

    async def buy_number(self, country: str, service: str, operator=None):
        self.bought += 1
        return PurchasedNumber(
            provider_order_id=f"{self.provider.value}-1",
            phone_number=f"+1{self.bought}",
            cost_usd=self.price,
            raw={},
        )

    async def get_balance(self):
        return Decimal("100")

    async def check_status(self, order_id: str):
        raise NotImplementedError

    async def cancel_order(self, order_id: str):
        return True

    async def finish_order(self, order_id: str):
        return True

    async def get_countries_services(self):
        return []


@pytest.mark.asyncio
async def test_smart_number_routing_prefers_reliable_provider_over_slightly_cheaper_bad_one():
    async with async_session_maker() as session:
        service = NumberService(
            code="tg",
            name_ar="Telegram",
            fivesim_code="telegram",
            smshub_code="tg",
        )
        country = Country(
            code="tr",
            name_ar="تركيا",
            fivesim_code="turkey",
            smshub_code="0",
            is_active=True,
        )
        buyer = User(telegram_id=777, balance=Decimal("10"))
        session.add_all([service, country, buyer])
        await session.flush()
        for provider in (ProviderName.FIVESIM, ProviderName.SMSHUB):
            status = await session.get(ProviderStatus, provider)
            if status is None:
                status = ProviderStatus(provider=provider)
                session.add(status)
            status.is_online = True
            status.last_checked_at = None

        now = datetime.utcnow()
        for index in range(6):
            session.add(
                NumberOrder(
                    user_id=buyer.id,
                    provider=ProviderName.FIVESIM,
                    provider_order_id=f"bad-{index}",
                    service="tg",
                    country_code="tr",
                    phone_number=f"+90bad{index}",
                    price_provider_usd=Decimal("1.00"),
                    price_sell_usd=Decimal("1.50"),
                    status=OrderStatus.REFUNDED,
                    purchased_at=now - timedelta(minutes=index + 1),
                )
            )
            session.add(
                NumberOrder(
                    user_id=buyer.id,
                    provider=ProviderName.SMSHUB,
                    provider_order_id=f"good-{index}",
                    service="tg",
                    country_code="tr",
                    phone_number=f"+90good{index}",
                    price_provider_usd=Decimal("1.05"),
                    price_sell_usd=Decimal("1.55"),
                    status=OrderStatus.COMPLETED,
                    purchased_at=now - timedelta(minutes=index + 1),
                    completed_at=now - timedelta(minutes=index),
                )
            )
        await session.commit()

        manager = ProviderManager()
        manager._providers = {
            ProviderName.FIVESIM: _FakeNumberProvider(ProviderName.FIVESIM, Decimal("1.00")),
            ProviderName.SMSHUB: _FakeNumberProvider(ProviderName.SMSHUB, Decimal("1.05")),
        }

        result = await manager.buy_number(service, country, session=session)

        assert result.provider == ProviderName.SMSHUB


@pytest.mark.asyncio
async def test_dynamic_main_menu_buttons_can_be_added_toggled_and_deleted():
    from services.main_button_service import MainButtonService

    async with async_session_maker() as session:
        await MainButtonService.reset_defaults(session)
        defaults = await MainButtonService.list_buttons(include_inactive=True)
        assert any(button.action == "store:home" for button in defaults)

        button = await MainButtonService.add(session, "🎮 ألعاب", "store:section:games")
        buttons = await MainButtonService.list_buttons(include_inactive=True)
        assert any(item.id == button.id and item.action == "store:section:games" for item in buttons)

        toggled = await MainButtonService.toggle(session, button.id)
        assert toggled is not None and toggled.is_active is False
        active = await MainButtonService.list_buttons(include_inactive=False)
        assert all(item.id != button.id for item in active)

        assert await MainButtonService.delete(session, button.id) is True
        buttons = await MainButtonService.list_buttons(include_inactive=True)
        assert all(item.id != button.id for item in buttons)


@pytest.mark.asyncio
async def test_fixed_store_categories_are_seeded_once():
    from database.models import Category

    async with async_session_maker() as session:
        categories = list((await session.execute(select(Category))).scalars().all())
        names = {category.name_ar for category in categories}
        assert "قسم الرشق" in names
        assert "قسم شحن الألعاب" in names
        assert "قسم شحن التطبيقات" in names
        assert "قسم الأرصدة" in names
        assert "قسم البطاقات والفيز" in names
        assert "قسم الاشتراكات الرقمية" in names
        assert "قسم توثيق الحسابات" in names
        assert "قسم الأكواد الرقمية" in names


@pytest.mark.asyncio
async def test_notification_center_preferences_templates_and_dedupe():
    from services.notification_center_service import NotificationCenterService

    async with async_session_maker() as session:
        user = User(telegram_id=99001, balance=Decimal("0"))
        session.add(user)
        await session.commit()
        await session.refresh(user)

        assert await NotificationCenterService.should_send(session, user.id, "promotion") is True
        assert await NotificationCenterService.set_preference(session, user.id, "promotion", False) is True
        assert await NotificationCenterService.should_send(session, user.id, "promotion") is False
        assert await NotificationCenterService.set_preference(session, user.id, "payment", False) is False
        assert await NotificationCenterService.should_send(session, user.id, "payment", "critical") is True

        await NotificationCenterService.record(
            session,
            user_id=user.id,
            recipient_chat_id=user.telegram_id,
            category="order",
            priority="high",
            title="طلب",
            body="تم إنشاء الطلب",
            status="sent",
            dedupe_key="order:1",
        )
        assert await NotificationCenterService.recently_sent(session, "order:1") is True
        inbox = await NotificationCenterService.inbox(session, user.id)
        assert inbox and inbox[0].title == "طلب"
        assert await NotificationCenterService.mark_read(session, user.id) == 1

        templates = await NotificationCenterService.templates(session)
        keys = {template.key for template in templates}
        assert "deposit_approved" in keys
