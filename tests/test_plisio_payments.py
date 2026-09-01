from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from config import settings
from database.engine import async_session_maker
from database.models import AutoInvoice, AutoInvoiceMethod, AutoInvoiceStatus, Transaction, User
from handlers import deposit_methods
from services.payment_method_service import diagnose_payment_method
from services.plisio_service import PlisioAPIError, PlisioClient
from services.settings_service import SettingsService
from keyboards.deposit_methods import usdt_networks_kb
from tasks import invoice_monitor


class _FakeResponse:
    def __init__(self, status: int, payload: object):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def text(self) -> str:
        return json.dumps(self._payload)


class _FakeSession:
    closed = False

    def __init__(self, response: _FakeResponse):
        self.response = response
        self.last_url = None
        self.last_params = None

    def get(self, url, params=None):
        self.last_url = url
        self.last_params = params
        return self.response

    def post(self, url, data=None):
        self.last_url = url
        self.last_params = data
        return self.response


def test_auto_usdt_keyboard_matches_enabled_plisio_usdt_networks():
    keyboard = usdt_networks_kb("auto")
    callback_data = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }

    assert "usdt_net:auto:TRC20" in callback_data
    assert "usdt_net:auto:BEP20" in callback_data
    assert "usdt_net:auto:BNB" not in callback_data
    assert "usdt_net:auto:ERC20" not in callback_data


@pytest.mark.asyncio
async def test_auto_usdt_amount_uses_selected_network_and_plisio_currency(monkeypatch):
    class _FakeState:
        async def get_data(self):
            return {"network": "BEP20", "plisio_currency": "USDT_BSC"}

        async def clear(self):
            return None

    class _FakeMessage:
        text = "10"

        def __init__(self):
            self.answers = []

        async def answer(self, text, **kwargs):
            self.answers.append((text, kwargs))
            return SimpleNamespace(chat=SimpleNamespace(id=88001), message_id=77)

    monkeypatch.setattr(
        deposit_methods,
        "_require_payment_method",
        AsyncMock(return_value=True),
    )

    async def get_decimal(key, _default):
        return {
            "min_deposit_usdt_usd": Decimal("2"),
            "plisio_max_amount_usd": Decimal("500"),
            "plisio_fee_percent": Decimal("0"),
        }[key]

    monkeypatch.setattr(deposit_methods.SettingsService, "get_decimal", get_decimal)
    create_payment = AsyncMock(
        return_value={
            "uuid": "txn-bep20-flow",
            "url": "https://plisio.example/invoice/txn-bep20-flow",
            "address": None,
            "payer_amount": None,
            "expired_at": None,
        }
    )
    monkeypatch.setattr(deposit_methods.plisio_client, "create_payment", create_payment)

    async with async_session_maker() as session:
        user = User(telegram_id=88001, balance=Decimal("0"))
        session.add(user)
        await session.flush()
        message = _FakeMessage()

        await deposit_methods.usdt_auto_amount_received(
            message,
            _FakeState(),
            session,
            user,
            object(),
        )

        saved = (
            await session.execute(
                select(AutoInvoice).where(AutoInvoice.external_invoice_id == "txn-bep20-flow")
            )
        ).scalar_one()

    assert create_payment.await_args.kwargs["currency"] == "USDT_BSC"
    assert saved.network == "BEP20"
    assert any("BEP20" in text for text, _kwargs in message.answers)


@pytest.mark.asyncio
async def test_plisio_accepts_regular_hosted_invoice_without_wallet_hash():
    client = PlisioClient()
    request = AsyncMock(
        return_value={
            "txn_id": "txn-hosted-1",
            "invoice_url": "https://plisio.example/invoice/txn-hosted-1",
        }
    )
    client._request = request

    invoice = await client.create_payment(
        amount=Decimal("10.30"),
        order_id="deposit-1",
        currency="USDT_TRX",
        lifetime=1800,
    )

    assert invoice["uuid"] == "txn-hosted-1"
    assert invoice["url"] == "https://plisio.example/invoice/txn-hosted-1"
    assert invoice["address"] is None
    assert invoice["payer_amount"] is None
    assert invoice["payer_amount_known"] is False
    request.assert_awaited_once()
    assert request.await_args.args[:2] == ("GET", "/invoices/new")
    assert request.await_args.args[2]["expire_min"] == "30"


@pytest.mark.asyncio
async def test_plisio_keeps_white_label_fields_optional_but_normalized():
    client = PlisioClient()
    client._request = AsyncMock(
        return_value={
            "txn_id": "txn-white-label",
            "invoice_url": "https://plisio.example/invoice/txn-white-label",
            "wallet_hash": "TRON-WALLET",
            "amount": "10.55",
            "expire_utc": 1_900_000_000,
        }
    )

    invoice = await client.create_payment(Decimal("10"), "deposit-2")

    assert invoice["uuid"] == "txn-white-label"
    assert invoice["address"] == "TRON-WALLET"
    assert invoice["payer_amount"] == "10.55"
    assert invoice["payer_amount_known"] is True
    assert invoice["expired_at"] == 1_900_000_000


@pytest.mark.asyncio
async def test_plisio_provider_error_is_preserved_without_secret_leak():
    client = PlisioClient()
    client.secret_key = "secret-key-that-must-not-appear"
    response = _FakeResponse(
        401,
        {
            "status": "error",
            "data": {"name": "Unauthorized", "message": "Invalid API key", "code": 401},
        },
    )
    fake_session = _FakeSession(response)
    client._get_session = AsyncMock(return_value=fake_session)

    with pytest.raises(PlisioAPIError, match="Invalid API key") as raised:
        await client._request("GET", "/invoices/new", {"source_amount": "10"})

    assert client.secret_key not in str(raised.value)
    assert fake_session.last_params["api_key"] == client.secret_key


@pytest.mark.asyncio
async def test_payment_diagnostics_explain_missing_shamcash_credentials_and_recover():
    await SettingsService.reload()
    async with async_session_maker() as session:
        await SettingsService.set(session, "payment_shamcash_auto_enabled", "true")

    monkeypatch_values = {
        "SAM_API_KEY": settings.SAM_API_KEY,
        "SAM_API_WALLET_ADDRESS": settings.SAM_API_WALLET_ADDRESS,
    }
    try:
        settings.SAM_API_KEY = ""
        settings.SAM_API_WALLET_ADDRESS = ""
        missing = await diagnose_payment_method("shamcash_auto")
        assert missing.enabled is False
        assert missing.configured is False
        assert "SAM_API_KEY" in missing.reason
        assert "SAM_API_WALLET_ADDRESS" in missing.reason

        settings.SAM_API_KEY = "staging-key"
        settings.SAM_API_WALLET_ADDRESS = "staging-wallet"
        ready = await diagnose_payment_method("shamcash_auto")
        assert ready.enabled is True
        assert ready.configured is True
    finally:
        settings.SAM_API_KEY = monkeypatch_values["SAM_API_KEY"]
        settings.SAM_API_WALLET_ADDRESS = monkeypatch_values["SAM_API_WALLET_ADDRESS"]


class _FakeNotifier:
    async def notify_user(self, *_args, **_kwargs):
        return True

    async def notify_admin(self, *_args, **_kwargs):
        return None


async def _create_invoice(session, *, expires_at=None) -> AutoInvoice:
    user = User(telegram_id=88001, balance=Decimal("0"))
    session.add(user)
    await session.flush()
    invoice = AutoInvoice(
        user_id=user.id,
        method=AutoInvoiceMethod.USDT_AUTO,
        external_invoice_id="txn-monitor-1",
        amount_usd=Decimal("5"),
        amount_original=Decimal("5"),
        currency="USDT",
        network="TRC20",
        status=AutoInvoiceStatus.PENDING,
        expires_at=expires_at or datetime.utcnow() + timedelta(minutes=30),
    )
    session.add(invoice)
    await session.commit()
    await session.refresh(invoice)
    return invoice


@pytest.mark.asyncio
async def test_paid_invoice_adds_balance_once_even_when_processing_is_retried(monkeypatch):
    monkeypatch.setattr(invoice_monitor, "NotificationService", lambda _bot: _FakeNotifier())
    async with async_session_maker() as session:
        invoice = await _create_invoice(session)
        await invoice_monitor._process_paid_usdt_invoice(
            session,
            invoice,
            {"status": "paid", "raw": {"status": "completed"}},
            object(),
        )
        await invoice_monitor._process_paid_usdt_invoice(
            session,
            invoice,
            {"status": "paid", "raw": {"status": "completed"}},
            object(),
        )

        user = await session.get(User, invoice.user_id)
        transactions = (
            await session.execute(
                select(Transaction).where(Transaction.payment_reference == f"invoice:{invoice.id}")
            )
        ).scalars().all()
        await session.refresh(invoice)

        assert user.balance == Decimal("5.0000")
        assert len(transactions) == 1
        assert invoice.status == AutoInvoiceStatus.PAID


@pytest.mark.asyncio
async def test_expired_invoice_stays_pending_when_final_provider_check_fails(monkeypatch):
    async with async_session_maker() as session:
        invoice = await _create_invoice(session, expires_at=datetime.utcnow() - timedelta(seconds=1))

        async def provider_failure(*_args, **_kwargs):
            raise PlisioAPIError("temporary provider failure")

        monkeypatch.setattr(invoice_monitor.plisio_client, "get_payment_info", provider_failure)
        await invoice_monitor._expire_invoice(session, invoice, object())
        await session.refresh(invoice)

        assert invoice.status == AutoInvoiceStatus.PENDING


@pytest.mark.asyncio
async def test_expired_invoice_is_closed_when_provider_confirms_it_is_not_paid(monkeypatch):
    monkeypatch.setattr(invoice_monitor, "NotificationService", lambda _bot: _FakeNotifier())
    async with async_session_maker() as session:
        invoice = await _create_invoice(session, expires_at=datetime.utcnow() - timedelta(seconds=1))

        async def provider_pending(*_args, **_kwargs):
            return {"status": "process"}

        monkeypatch.setattr(invoice_monitor.plisio_client, "get_payment_info", provider_pending)
        await invoice_monitor._expire_invoice(session, invoice, object())
        await session.refresh(invoice)

        assert invoice.status == AutoInvoiceStatus.EXPIRED
