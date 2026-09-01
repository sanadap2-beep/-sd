from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    CartItem,
    Category,
    CategoryType,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    SubCategory,
    User,
)
from services.cart_service import CartService
from services.encryption_service import EncryptionService
from services.input_validation_service import InputValidationError, InputValidationService
from services.operation_lock_service import OperationLockService
from services.price_cache_service import PriceCacheService
from services.price_lock_service import PriceLockService
from services.receipt_service import ReceiptService
from services.reseller_api_service import ResellerAPIService
from services.tiered_pricing_service import TieredPricingService


@pytest.mark.asyncio
async def test_input_cache_lock_and_encryption():
    assert InputValidationService.positive_money("1.25") == Decimal("1.25")
    assert InputValidationService.url("https://example.com/path")
    with pytest.raises(InputValidationError):
        InputValidationService.positive_money("NaN")
    encrypted = EncryptionService.encrypt("secret-value")
    assert encrypted != "secret-value"
    assert EncryptionService.decrypt(encrypted) == "secret-value"
    await PriceCacheService.invalidate()
    await PriceCacheService.set("test", {"price": Decimal("1")}, ttl=10)
    assert (await PriceCacheService.get("test"))["price"] == Decimal("1")
    quote = await PriceLockService.create("telegram", "nl", "fake", Decimal("1"), Decimal("2"))
    assert (await PriceLockService.get(quote.token, "telegram", "nl")).sell_price_usd == Decimal(
        "2"
    )
    await PriceLockService.consume(quote.token)
    assert await PriceLockService.get(quote.token, "telegram", "nl") is None
    async with OperationLockService.acquire("test-lock"):
        assert True


@pytest.mark.asyncio
async def test_cart_reseller_tiered_pricing_and_receipt():
    async with async_session_maker() as session:
        user = User(telegram_id=71, full_name="User")
        category = Category(name_ar="SMM", emoji="📈", type=CategoryType.SMM)
        session.add_all([user, category])
        await session.flush()
        sub = SubCategory(category_id=category.id, name_ar="Social", emoji="📈")
        session.add(sub)
        await session.flush()
        product = Product(
            sub_category_id=sub.id,
            name_ar="Followers",
            price_usd=Decimal("2"),
            cost_price_usd=Decimal("1"),
            status=ProductStatus.ACTIVE,
            fulfillment_type=ProductFulfillmentType.API,
            requires_link=True,
            requires_quantity=True,
            min_quantity=100,
            max_quantity=10000,
        )
        session.add(product)
        await session.commit()
        await session.refresh(user)
        await session.refresh(product)
        item = await CartService.add(session, user.id, product.id, "https://example.com", 100)
        assert item.quantity == 100
        assert CartService.total(await CartService.get_items(session, user.id)) == Decimal("2.0000")
        discount, _ = await TieredPricingService.discount_for(
            session, user.id, product, Decimal("2"), 1000
        )
        assert discount == Decimal("0.0400")
        receipt = ReceiptService.unified_text(
            type(
                "Order",
                (),
                {
                    "id": 1,
                    "status": type("Status", (), {"value": "completed"})(),
                    "price_usd": Decimal("2"),
                    "quantity": 1,
                    "target": "x",
                    "created_at": datetime.utcnow(),
                },
            )(),
            user,
            product,
        )
        assert "إيصال" in receipt
        account, raw_key = await ResellerAPIService.create_account(session, "Partner", user.id)
        assert account.id and raw_key.startswith("rsl_")


@pytest.mark.asyncio
async def test_cart_item_is_updated_not_duplicated():
    async with async_session_maker() as session:
        user = User(telegram_id=72)
        category = Category(name_ar="App", emoji="📦", type=CategoryType.APPS)
        session.add_all([user, category])
        await session.flush()
        sub = SubCategory(category_id=category.id, name_ar="Tools", emoji="🧰")
        session.add(sub)
        await session.flush()
        product = Product(
            sub_category_id=sub.id,
            name_ar="Tool",
            price_usd=Decimal("1"),
            cost_price_usd=Decimal("1"),
            status=ProductStatus.ACTIVE,
            fulfillment_type=ProductFulfillmentType.INVENTORY,
        )
        session.add(product)
        await session.commit()
        await session.refresh(user)
        await session.refresh(product)
        await CartService.add(session, user.id, product.id)
        await CartService.add(session, user.id, product.id)
        count = (
            (await session.execute(select(CartItem.id).where(CartItem.user_id == user.id)))
            .scalars()
            .all()
        )
        assert len(count) == 1
