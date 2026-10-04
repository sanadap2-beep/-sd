"""White-label tenants: isolation, pricing, wallet, billing, mirror purchase."""

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
    SubCategory,
    SupportTicket,
    SupportTicketStatus,
    Tenant,
    TenantOrderMap,
    Transaction,
    User,
)
from services.inventory_service import InventoryService
from services.tenant_order_service import TenantOrderService
from services.tenant_service import TenantError, TenantService, token_hash

FAKE_TOKEN = "123456789:AAHfakeFakeFakeFakeFakeFakeFakeFake123"


async def _owner(session, tg=501001) -> User:
    u = User(tenant_id=0, telegram_id=tg, full_name="merchant", is_activated=True)
    session.add(u)
    await session.commit()
    await session.refresh(u)
    return u


async def _tenant(session, owner_id, margin="25", token=FAKE_TOKEN) -> Tenant:
    return await TenantService.create(
        session,
        owner_user_id=owner_id,
        token=token,
        bot_username="SubStoreBot",
        brand_name="متجر الفرع",
        margin_percent=Decimal(margin),
    )


async def _catalog(session):
    cat = Category(name_ar="ألعاب", type=CategoryType.GAMES, is_active=True)
    session.add(cat)
    await session.flush()
    sub = SubCategory(category_id=cat.id, name_ar="شحن", is_active=True)
    session.add(sub)
    await session.flush()
    prod = Product(
        sub_category_id=sub.id,
        name_ar="شدات 60",
        price_usd=Decimal("10"),
        cost_price_usd=Decimal("8"),
        fulfillment_type=ProductFulfillmentType.INVENTORY,
        status=ProductStatus.ACTIVE,
    )
    session.add(prod)
    await session.commit()
    await session.refresh(prod)
    return cat, sub, prod


async def test_tenant_create_encrypts_token(fresh_database):
    async with async_session_maker() as session:
        owner = await _owner(session)
        t = await _tenant(session, owner.id)
        assert t.token_encrypted and FAKE_TOKEN not in t.token_encrypted
        assert t.token_hash == token_hash(FAKE_TOKEN)
        assert TenantService.reveal_token(t) == FAKE_TOKEN
        found = await TenantService.get_by_hash(session, token_hash(FAKE_TOKEN))
        assert found.id == t.id


async def test_tenant_duplicate_token_rejected(fresh_database):
    async with async_session_maker() as session:
        owner = await _owner(session)
        await _tenant(session, owner.id)
        with pytest.raises(TenantError):
            await _tenant(session, owner.id)


async def test_cross_tenant_same_telegram_id(fresh_database):
    async with async_session_maker() as session:
        owner = await _owner(session, tg=601001)
        t = await _tenant(session, owner.id)
        session.add(User(tenant_id=0, telegram_id=777001, full_name="main"))
        session.add(User(tenant_id=t.id, telegram_id=777001, full_name="sub"))
        await session.commit()
        rows = (
            await session.execute(select(User).where(User.telegram_id == 777001))
        ).scalars().all()
        assert {u.tenant_id for u in rows} == {0, t.id}


async def test_margin_pricing_math(fresh_database):
    async with async_session_maker() as session:
        owner = await _owner(session, tg=602001)
        t = await _tenant(session, owner.id, margin="25")
        price = await TenantService.merchant_price(session, t, Decimal("10"))
        assert price == Decimal("12.5000")
        fee = TenantService.platform_fee(Decimal("10"), t)
        assert fee == Decimal("0.2000")


async def test_wallet_fund_charge_atomic(fresh_database):
    async with async_session_maker() as session:
        owner = await _owner(session, tg=603001)
        t = await _tenant(session, owner.id)
        w = await TenantService.fund(session, t.id, Decimal("50"))
        assert w.balance == Decimal("50")
        w = await TenantService.charge(session, t.id, Decimal("20"), Decimal("5"))
        assert w.balance == Decimal("30")
        with pytest.raises(TenantError):
            await TenantService.charge(session, t.id, Decimal("1000"))


async def test_subscription_billing_cycle(fresh_database):
    async with async_session_maker() as session:
        owner = await _owner(session, tg=604001)
        rich = await _tenant(session, owner.id, token=FAKE_TOKEN)
        poor = await TenantService.create(
            session, owner_user_id=owner.id, token="987654321:BBBfakeFakeFakeFakeFakeFakeFakeFake456",
            bot_username="PoorBot", brand_name="فقير", margin_percent=Decimal("10"),
        )
        await TenantService.fund(session, rich.id, Decimal("100"))
        rich_id, poor_id = rich.id, poor.id
        for t in (rich, poor):
            t.subscription_due_at = datetime.utcnow() - timedelta(hours=1)
        await session.commit()

        report = await TenantService.bill_due(session)
        assert rich_id in report["billed"]
        assert poor_id in report["graced"]

        # poor يتجاوز السماح → تجميد
        poor = await session.get(Tenant, poor_id)
        poor.subscription_due_at = datetime.utcnow() - timedelta(hours=1)
        await session.commit()
        report = await TenantService.bill_due(session)
        assert poor_id in report["suspended"]
        poor = await session.get(Tenant, poor_id)
        await session.refresh(poor)
        assert poor.subscription_status == "suspended"


async def test_mirror_inventory_purchase_money_loop(fresh_database):
    async with async_session_maker() as session:
        owner = await _owner(session, tg=605001)
        t = await _tenant(session, owner.id, margin="25")
        _cat, _sub, prod = await _catalog(session)
        await InventoryService.add_item(session, prod.id, "CODE-123")
        await TenantService.fund(session, t.id, Decimal("100"))

        sub_user = User(tenant_id=t.id, telegram_id=888001, full_name="cust", is_activated=True,
                        balance=Decimal("50"))
        session.add(sub_user)
        await session.commit()
        await session.refresh(sub_user)

        quote = await TenantOrderService.quote(session, t, prod.id, 1)
        assert quote["merchant_total"] == Decimal("12.5000")

        result = await TenantOrderService.purchase(session, t, sub_user, prod.id)
        assert result.order.status.value == "completed"
        assert result.delivery_value == "CODE-123"

        await session.refresh(sub_user)
        assert sub_user.balance == Decimal("50") - Decimal("12.5000")

        wallet = await TenantService.wallet(session, t.id)
        # المحفظة: -10 أساسي -0.2 عمولة
        assert wallet.balance == Decimal("100") - Decimal("10.2000")

        # دفتر كل طرف بنطاقه
        txs = (await session.execute(select(Transaction))).scalars().all()
        assert any(x.tenant_id == t.id for x in txs)
        mapping = (
            await session.execute(
                select(TenantOrderMap).where(TenantOrderMap.sub_order_id == result.order.id)
            )
        ).scalar_one_or_none()
        assert mapping is not None and mapping.base_price_usd == Decimal("10")


async def test_tenant_tickets_routed_to_merchant(fresh_database):
    async with async_session_maker() as session:
        owner = await _owner(session, tg=606001)
        t = await _tenant(session, owner.id)
        u = User(tenant_id=t.id, telegram_id=888002, full_name="cust2", is_activated=True)
        session.add(u)
        await session.flush()
        session.add(SupportTicket(
            tenant_id=t.id, user_id=u.id, subject="مشكلة",
            message="التفاصيل", status=SupportTicketStatus.OPEN,
        ))
        session.add(SupportTicket(
            tenant_id=0, user_id=owner.id, subject="رئيسية",
            message="x", status=SupportTicketStatus.OPEN,
        ))
        await session.commit()
        merchant_view = (
            await session.execute(
                select(SupportTicket).where(SupportTicket.tenant_id == t.id)
            )
        ).scalars().all()
        assert len(merchant_view) == 1 and merchant_view[0].subject == "مشكلة"
        platform_view = (
            await session.execute(
                select(SupportTicket).where(SupportTicket.tenant_id == 0)
            )
        ).scalars().all()
        assert all(x.tenant_id == 0 for x in platform_view)


async def test_selective_catalog_visibility(fresh_database):
    async with async_session_maker() as session:
        owner = await _owner(session, tg=607001)
        t = await _tenant(session, owner.id)
        t.catalog_mode = "selective"
        cat, _sub, prod = await _catalog(session)
        # قبل الاختيار: مخفي
        assert await TenantOrderService.is_visible(session, t, prod) is False
        from database.models import TenantCatalogSelection

        session.add(TenantCatalogSelection(
            tenant_id=t.id, item_type="category", item_id=cat.id))
        await session.commit()
        assert await TenantOrderService.is_visible(session, t, prod) is True
        t.catalog_mode = "full"
        assert await TenantOrderService.is_visible(session, t, prod) is True
