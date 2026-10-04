"""P0 money guards: no free value, no duplicate spend, honest settlement.

تعمل على SQLite وPostgreSQL (وظيفة CI الثانية).
"""

from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.engine import async_session_maker
from database.models import (
    ApiProvider,
    ApiProviderType,
    ApiProtocolType,
    AutoInvoiceMethod,
    Category,
    CategoryType,
    DepositRequest,
    DepositStatus,
    NumberOrder,
    OrderStatus,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    ProviderName,
    SpinHistory,
    SpinPrize,
    SubCategory,
    SupportTicket,
    Transaction,
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from services.balance_service import BalanceService
from services.checkout_service import CheckoutError, CheckoutService


async def _user(session, tg, balance="0"):
    u = User(tenant_id=0, telegram_id=tg, full_name="t", is_activated=True,
             balance=Decimal(balance))
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


async def _digital_product(session, price="15", provider_balance="1"):
    cat = Category(name_ar="اشتراكات", type=CategoryType.SUBSCRIPTIONS, is_active=True)
    session.add(cat)
    await session.flush()
    sub = SubCategory(category_id=cat.id, name_ar="منصات", is_active=True)
    session.add(sub)
    await session.flush()
    prov = ApiProvider(
        name="ggsoma-test", type=ApiProviderType.SUBSCRIPTIONS,
        protocol_type=ApiProtocolType.CUSTOM, api_url="https://example.invalid",
        api_key="k", balance=Decimal(provider_balance), rate_to_usd=Decimal("1"),
        is_active=True, custom_config='{"engine": "ggsoma"}',
    )
    session.add(prov)
    await session.flush()
    prod = Product(
        sub_category_id=sub.id, api_provider_id=prov.id,
        provider_service_id="svc_1", name_ar="اشتراك شهر",
        price_usd=Decimal(price), cost_price_usd=Decimal("10"),
        fulfillment_type=ProductFulfillmentType.API,
        status=ProductStatus.ACTIVE,
    )
    session.add(prod)
    await session.commit()
    await session.refresh(prod)
    return prod


async def test_low_provider_balance_grants_nothing(fresh_database):
    """رصيد مزود منخفض → رفض بلا طلب ولا كاشباك ولا نقاط ولا استهلاك عرض."""
    async with async_session_maker() as session:
        user = await _user(session, 701001, "0")
        prod = await _digital_product(session)
        with pytest.raises(CheckoutError):
            await CheckoutService.purchase(session, user.id, prod.id, "target", 1)
        orders = (await session.execute(select(UnifiedOrder))).scalars().all()
        assert orders == []
        txs = (
            await session.execute(select(Transaction).where(Transaction.user_id == user.id))
        ).scalars().all()
        assert txs == []
        await session.refresh(user)
        assert user.balance == Decimal("0")
        assert (user.loyalty_points or 0) == 0


async def test_admin_refund_requires_original_debit(fresh_database):
    """استرجاع إداري بلا خصم أصلي مرفوض — وبخصم مقبول."""
    from handlers.admin import orders as admin_orders

    answers = []

    class FakeMessage:
        async def edit_text(self, *a, **k):
            return None

    class FakeCallback:
        def __init__(self, oid):
            self.data = f"admin:order_refund:{oid}"
            self.message = FakeMessage()
            self.from_user = SimpleNamespace(id=1)

        async def answer(self, text="", show_alert=False):
            answers.append(text)

    async with async_session_maker() as session:
        user = await _user(session, 702001, "10")
        order = UnifiedOrder(
            user_id=user.id, tenant_id=0, price_usd=Decimal("5"),
            cost_price_usd=Decimal("4"), status=UnifiedOrderStatus.PENDING,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        cb = FakeCallback(order.id)
        await admin_orders.order_refund(cb, session, bot=SimpleNamespace())
        assert any("لا توجد حركة خصم" in a for a in answers), answers
        await session.refresh(order)
        assert order.status == UnifiedOrderStatus.PENDING

        # الآن بخصم أصلي: يُقبل
        await BalanceService.deduct_balance(
            session, user.id, Decimal("5"), TransactionType.PURCHASE,
            description="شراء", related_table="unified_orders", related_id=order.id,
        )
        answers.clear()

        class FakeNotifier:
            def __init__(self, *a, **k):
                pass

            async def notify_user(self, *a, **k):
                return None

        import handlers.admin.orders as mod

        old = mod.NotificationService
        mod.NotificationService = FakeNotifier
        try:
            cb2 = FakeCallback(order.id)
            await mod.order_refund(cb2, session, bot=SimpleNamespace())
        finally:
            mod.NotificationService = old
        await session.refresh(order)
        assert order.status == UnifiedOrderStatus.REFUNDED
        refund = (
            await session.execute(
                select(Transaction).where(
                    Transaction.user_id == user.id,
                    Transaction.type == TransactionType.REFUND,
                )
            )
        ).scalar_one_or_none()
        assert refund is not None and refund.amount == Decimal("5")


async def test_number_intent_key_unique(fresh_database):
    async with async_session_maker() as session:
        user = await _user(session, 703001, "10")
        o1 = NumberOrder(
            user_id=user.id, tenant_id=0, provider=ProviderName.FIVESIM,
            provider_order_id="pending:k1", service="tg", country_code="ru",
            phone_number="pending", price_provider_usd=Decimal("1"),
            price_sell_usd=Decimal("2"), status=OrderStatus.PENDING,
            idempotency_key="k1",
        )
        session.add(o1)
        await session.commit()
        o2 = NumberOrder(
            user_id=user.id, tenant_id=0, provider=ProviderName.FIVESIM,
            provider_order_id="pending:k1", service="tg", country_code="ru",
            phone_number="pending", price_provider_usd=Decimal("1"),
            price_sell_usd=Decimal("2"), status=OrderStatus.PENDING,
            idempotency_key="k1",
        )
        session.add(o2)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_spin_daily_unique(fresh_database, monkeypatch):
    from services.spin_service import SpinService

    async def _on():
        return True

    monkeypatch.setattr(SpinService, "enabled", staticmethod(_on))
    async with async_session_maker() as session:
        user = await _user(session, 704001, "0")
        session.add(SpinPrize(name_ar="جائزة", prize_type="nothing", value=Decimal("0")))
        await session.commit()
        first = await SpinService.spin(session, user.id)
        assert first
        with pytest.raises(Exception):
            await SpinService.spin(session, user.id)


async def test_deposit_proof_unique(fresh_database):
    async with async_session_maker() as session:
        user = await _user(session, 705001, "0")
        session.add(DepositRequest(
            user_id=user.id, tenant_id=0, amount_usd=Decimal("5"),
            proof_tx_number="TX-UNIQUE-1", status=DepositStatus.PENDING,
        ))
        await session.commit()
        session.add(DepositRequest(
            user_id=user.id, tenant_id=0, amount_usd=Decimal("5"),
            proof_tx_number="TX-UNIQUE-1", status=DepositStatus.PENDING,
        ))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_plisio_mismatch_goes_to_review(fresh_database):
    from tasks.invoice_monitor import _needs_review

    inv = SimpleNamespace(amount_original=Decimal("10"))
    assert _needs_review(inv, {"raw_status": "mismatch", "amount": "10"}) is True
    assert _needs_review(inv, {"raw_status": "completed", "amount": "10"}) is False
    assert _needs_review(inv, {"raw_status": "completed", "amount": "9"}) is True
    assert _needs_review(inv, {"raw_status": "completed", "amount": "11"}) is True
    assert _needs_review(inv, {"raw_status": "completed", "amount": None}) is False


async def test_unknown_status_exists():
    assert OrderStatus.UNKNOWN.value == "unknown"
