"""جرد الحسابات يشمل المستخدمين فقط ويستثني الأدمن."""

from __future__ import annotations

from decimal import Decimal

import pytest

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    NumberOrder,
    OrderStatus,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    ProviderName,
    SubCategory,
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from services.balance_service import BalanceService
from services.ledger_service import LedgerService


async def _setup(session):
    admin = User(
        telegram_id=9001,
        full_name="Admin Buyer",
        username="admin_buyer",
        is_admin=True,
        is_activated=True,
    )
    customer = User(
        telegram_id=9002,
        full_name="أحمد علي",
        username="ahmad",
        is_admin=False,
        is_activated=True,
    )
    category = Category(name_ar="رشق", emoji="📈", type=CategoryType.SMM, is_active=True)
    session.add_all([admin, customer, category])
    await session.flush()
    sub = SubCategory(
        category_id=category.id, name_ar="إنستغرام", emoji="📸", is_active=True
    )
    session.add(sub)
    await session.flush()
    product = Product(
        sub_category_id=sub.id,
        name_ar="لايكات إنستا",
        price_usd=Decimal("2"),
        cost_price_usd=Decimal("0.5"),
        status=ProductStatus.ACTIVE,
        fulfillment_type=ProductFulfillmentType.API,
    )
    session.add(product)
    await session.commit()
    await session.refresh(admin)
    await session.refresh(customer)
    await session.refresh(product)
    return admin, customer, product


@pytest.mark.asyncio
async def test_ledger_excludes_admin_deposits_and_orders():
    async with async_session_maker() as session:
        admin, customer, product = await _setup(session)
        await BalanceService.add_balance(
            session,
            customer.id,
            Decimal("10"),
            TransactionType.DEPOSIT,
            description="شحن رصيد - شام كاش",
        )
        await BalanceService.add_balance(
            session,
            admin.id,
            Decimal("50"),
            TransactionType.DEPOSIT,
            description="شحن أدمن",
        )
        await BalanceService.add_balance(
            session,
            customer.id,
            Decimal("5"),
            TransactionType.ADMIN_ADD,
            description="إضافة رصيد يدوية من الأدمن",
        )
        session.add_all(
            [
                UnifiedOrder(
                    user_id=customer.id,
                    product_id=product.id,
                    price_usd=Decimal("2.0000"),
                    cost_price_usd=Decimal("0.5000"),
                    status=UnifiedOrderStatus.COMPLETED,
                    quantity=1,
                ),
                UnifiedOrder(
                    user_id=admin.id,
                    product_id=product.id,
                    price_usd=Decimal("20.0000"),
                    cost_price_usd=Decimal("1.0000"),
                    status=UnifiedOrderStatus.COMPLETED,
                    quantity=1,
                ),
                NumberOrder(
                    user_id=customer.id,
                    provider=ProviderName.FIVESIM,
                    provider_order_id="c1",
                    service="telegram",
                    country_code="sy",
                    phone_number="+963999",
                    price_provider_usd=Decimal("0.2000"),
                    price_sell_usd=Decimal("0.8000"),
                    status=OrderStatus.COMPLETED,
                ),
                NumberOrder(
                    user_id=admin.id,
                    provider=ProviderName.FIVESIM,
                    provider_order_id="a1",
                    service="telegram",
                    country_code="sy",
                    phone_number="+963000",
                    price_provider_usd=Decimal("0.2000"),
                    price_sell_usd=Decimal("9.0000"),
                    status=OrderStatus.COMPLETED,
                ),
            ]
        )
        await session.commit()

        summary = await LedgerService.summary(session, "all")
        assert summary.deposit_total == Decimal("10.0000")
        assert summary.deposit_count == 1
        assert summary.sales_total == Decimal("2.8000")
        assert summary.cost_total == Decimal("0.7000")
        assert summary.profit_total == Decimal("2.1000")
        assert summary.order_count == 2

        deposits, total = await LedgerService.list_deposits(session, "all")
        assert total == 1
        assert deposits[0].telegram_id == 9002
        assert deposits[0].amount == Decimal("10.0000")
        assert "شام كاش" in (deposits[0].description or "")

        services, count, sales, cost, profit = await LedgerService.service_profits(
            session, "all"
        )
        assert count == 2
        by_name = {row.name: row for row in services}
        assert "لايكات إنستا" in by_name
        insta = by_name["لايكات إنستا"]
        assert insta.sales == Decimal("2.0000")
        assert insta.cost == Decimal("0.5000")
        assert insta.profit == Decimal("1.5000")
        assert insta.orders == 1
        assert sales == Decimal("2.8000")
        assert cost == Decimal("0.7000")
        assert profit == Decimal("2.1000")


def test_admin_keyboard_has_ledger_button():
    from keyboards.admin import admin_main_kb

    kb = admin_main_kb()
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    datas = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "📒 جرد الحسابات" in labels
    assert "admin:ledger" in datas
