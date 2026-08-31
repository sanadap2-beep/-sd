from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    PromotionDiscountType,
    SubCategory,
    Transaction,
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from services.assistant_service import AssistantService
from services.balance_service import BalanceService
from services.checkout_service import CheckoutService
from services.inventory_service import InventoryService
from services.gift_service import GiftService
from services.loyalty_service import LoyaltyService
from services.product_request_service import ProductRequestService
from services.promotion_service import PromotionService
from services.review_service import ReviewService


async def make_user(session, telegram_id: int) -> User:
    user = User(telegram_id=telegram_id, full_name=f"User {telegram_id}")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def make_product(session, user_id: int, name: str = "AI License") -> Product:
    category = Category(
        name_ar="Digital",
        emoji="📦",
        type=CategoryType.APPS,
        is_active=True,
    )
    session.add(category)
    await session.flush()
    subcategory = SubCategory(
        category_id=category.id,
        name_ar="AI",
        emoji="🤖",
        is_active=True,
    )
    session.add(subcategory)
    await session.flush()
    product = Product(
        sub_category_id=subcategory.id,
        name_ar=name,
        price_usd=Decimal("2"),
        cost_price_usd=Decimal("1"),
        status=ProductStatus.ACTIVE,
        fulfillment_type=ProductFulfillmentType.INVENTORY,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


@pytest.mark.asyncio
async def test_balance_transfer_is_atomic_and_idempotent():
    async with async_session_maker() as session:
        sender = await make_user(session, 11)
        recipient = await make_user(session, 12)
        await BalanceService.add_balance(
            session,
            sender.id,
            Decimal("10"),
            TransactionType.DEPOSIT,
            payment_reference="test-payment-1",
        )
        await BalanceService.add_balance(
            session,
            sender.id,
            Decimal("10"),
            TransactionType.DEPOSIT,
            payment_reference="test-payment-1",
        )
        await BalanceService.transfer(session, sender.id, recipient.id, Decimal("3"))
        await session.refresh(sender)
        await session.refresh(recipient)
        txs = (await session.execute(select(Transaction))).scalars().all()
        assert sender.balance == Decimal("7.0000")
        assert recipient.balance == Decimal("3.0000")
        assert len(txs) == 3


@pytest.mark.asyncio
async def test_purchase_debit_is_idempotent_with_reference():
    async with async_session_maker() as session:
        user = await make_user(session, 13)
        await BalanceService.add_balance(
            session,
            user.id,
            Decimal("10"),
            TransactionType.DEPOSIT,
            payment_reference="test-payment-2",
        )

        await BalanceService.deduct_balance(
            session,
            user.id,
            Decimal("2.5"),
            TransactionType.PURCHASE,
            payment_reference="purchase-attempt-13",
            is_purchase=True,
        )
        await BalanceService.deduct_balance(
            session,
            user.id,
            Decimal("2.5"),
            TransactionType.PURCHASE,
            payment_reference="purchase-attempt-13",
            is_purchase=True,
        )

        await session.refresh(user)
        purchases = (
            await session.execute(
                select(Transaction).where(
                    Transaction.payment_reference == "purchase-attempt-13"
                )
            )
        ).scalars().all()
        assert user.balance == Decimal("7.5000")
        assert user.total_spent_usd == Decimal("2.5000")
        assert user.total_orders == 1
        assert len(purchases) == 1


@pytest.mark.asyncio
async def test_gift_code_is_single_use():
    async with async_session_maker() as session:
        admin = await make_user(session, 21)
        user = await make_user(session, 22)
        gift = await GiftService.create(session, Decimal("5"), 1, admin.id)
        assert await GiftService.redeem(session, user.id, gift.code) == Decimal("5")
        with pytest.raises(Exception):
            await GiftService.redeem(session, user.id, gift.code)


@pytest.mark.asyncio
async def test_inventory_checkout_delivers_and_consumes_item():
    async with async_session_maker() as session:
        user = await make_user(session, 31)
        product = await make_product(session, user.id)
        await BalanceService.add_balance(
            session, user.id, Decimal("2"), TransactionType.DEPOSIT, payment_reference="seed-31"
        )
        await InventoryService.add_item(session, product.id, "official-license-code")
        checkout = await CheckoutService.purchase(session, user.id, product.id)
        order, value = checkout.order, checkout.delivery_value
        assert order.status == UnifiedOrderStatus.COMPLETED
        assert value == "official-license-code"
        assert await InventoryService.available_count(session, product.id) == 0


@pytest.mark.asyncio
async def test_inventory_checkout_is_idempotent_with_reference():
    async with async_session_maker() as session:
        user = await make_user(session, 32)
        product = await make_product(session, user.id, "Duplicate-safe License")
        await BalanceService.add_balance(
            session, user.id, Decimal("2"), TransactionType.DEPOSIT, payment_reference="seed-32"
        )
        await InventoryService.add_item(session, product.id, "same-license")

        first = await CheckoutService.purchase(
            session, user.id, product.id, payment_reference="checkout:32:retry-1"
        )
        second = await CheckoutService.purchase(
            session, user.id, product.id, payment_reference="checkout:32:retry-1"
        )

        await session.refresh(user)
        await session.refresh(product)
        purchases = (
            await session.execute(
                select(Transaction).where(
                    Transaction.payment_reference == "checkout:32:retry-1"
                )
            )
        ).scalars().all()
        assert first.order.id == second.order.id
        assert second.delivery_value == "same-license"
        assert user.balance == Decimal("0")
        assert user.total_orders == 1
        assert product.total_sold == 1
        assert len(purchases) == 1


@pytest.mark.asyncio
async def test_loyalty_daily_purchase_points_and_redemption():
    async with async_session_maker() as session:
        user = await make_user(session, 41)
        points, streak, already = await LoyaltyService.claim_daily(session, user.id)
        assert (points, streak, already) == (25, 1, False)
        assert (await LoyaltyService.claim_daily(session, user.id))[2] is True
        assert await LoyaltyService.award_points(session, user.id, 100, "once", "test", "test")
        assert not await LoyaltyService.award_points(session, user.id, 100, "once", "test", "test")
        amount = await LoyaltyService.redeem_points(session, user.id, 100)
        assert amount == Decimal("0.1")


@pytest.mark.asyncio
async def test_promotions_requests_reviews_and_assistant():
    async with async_session_maker() as session:
        owner = await make_user(session, 51)
        voter = await make_user(session, 52)
        product = await make_product(session, owner.id, "PUBG Coins")
        promotion = await PromotionService.create(
            session,
            product.id,
            "Launch Offer",
            PromotionDiscountType.PERCENT,
            Decimal("20"),
            datetime.utcnow(),
            datetime.utcnow() + timedelta(hours=1),
            owner.id,
        )
        best, discount = await PromotionService.get_best_promotion(
            session, product.id, Decimal("2")
        )
        assert best.id == promotion.id and discount == Decimal("0.4000")
        request, created = await ProductRequestService.create(
            session, owner.id, "PUBG Coins", "official"
        )
        assert created
        same, duplicate = await ProductRequestService.create(session, voter.id, " pubg   coins ")
        assert not duplicate and same.id == request.id and same.votes_count == 2
        order = UnifiedOrder(
            user_id=owner.id,
            product_id=product.id,
            price_usd=Decimal("2"),
            cost_price_usd=Decimal("1"),
            status=UnifiedOrderStatus.COMPLETED,
            completed_at=datetime.utcnow(),
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        await ReviewService.create(session, owner.id, order.id, 5, "great")
        result, _reason = await AssistantService.recommend(session, "PUBG")
        assert result[0].id == product.id
