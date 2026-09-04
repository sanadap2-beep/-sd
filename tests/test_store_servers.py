"""السيرفرات العامة: نظام واحد يخدم المتجر والرشق والألعاب."""

from decimal import Decimal

from database.engine import async_session_maker
from database.models import (
    ApiProvider,
    ApiProtocolType,
    ApiProviderType,
    Category,
    CategoryType,
    NumberService,
    NumberServer,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    SubCategory,
)
from services.store_server_service import StoreServerService
from services.product_service import ProductService
from services.number_server_service import NumberServerService
from keyboards.admin import admin_store_servers_kb, admin_ssvc_scope_kb, admin_nsvc_server_detail_kb


async def _seed_provider(session, name="مزود الاختبار") -> ApiProvider:
    provider = ApiProvider(
        name=name,
        type=ApiProviderType.SMM,
        protocol_type=ApiProtocolType.SMM_V2,
        api_url="https://example.test/api",
        api_key="key",
        is_active=True,
    )
    session.add(provider)
    await session.commit()
    await session.refresh(provider)
    return provider


async def test_create_and_list_for_scope():
    async with async_session_maker() as session:
        provider = await _seed_provider(session)
        server = await StoreServerService.create(
            session,
            scope="subcategory",
            scope_id=10,
            name_ar="سيرفر الاختبار",
            provider_kind="api",
            api_provider_id=provider.id,
            margin_percent=Decimal("25"),
        )
        active = await StoreServerService.active_for_scope(session, "subcategory", 10)
        all_servers = await StoreServerService.list_for_scope(session, "subcategory", 10, active_only=False)

    assert server.id > 0
    assert len(active) == 1
    assert len(all_servers) == 1
    assert active[0].name_ar == "سيرفر الاختبار"
    assert active[0].margin_percent == Decimal("25")


async def test_inactive_server_not_in_active_for_scope():
    async with async_session_maker() as session:
        provider = await _seed_provider(session)
        server = await StoreServerService.create(
            session,
            scope="global",
            scope_id=0,
            name_ar="سيرفر عام",
            provider_kind="api",
            api_provider_id=provider.id,
        )
        await StoreServerService.update(session, server.id, is_active=False)
        active = await StoreServerService.active_for_scope(session, "global", 0)
        all_servers = await StoreServerService.list_for_scope(session, "global", 0, active_only=False)

    assert len(active) == 0
    assert len(all_servers) == 1


async def test_filter_products_by_server_provider():
    async with async_session_maker() as session:
        p1 = await _seed_provider(session, "مزود 1")
        p2 = await _seed_provider(session, "مزود 2")
        prods = [
            type("P", (), {"api_provider_id": p1.id})(),
            type("P", (), {"api_provider_id": p2.id})(),
            type("P", (), {"api_provider_id": None})(),
        ]
        server = await StoreServerService.create(
            session,
            scope="subcategory",
            scope_id=1,
            name_ar="سيرفر 1",
            api_provider_id=p1.id,
            provider_kind="api",
        )
        filtered = await StoreServerService.filter_products(prods, server)

    assert [p.api_provider_id for p in filtered] == [p1.id]


async def test_unit_price_applies_margin_on_cost():
    async with async_session_maker() as session:
        server = await StoreServerService.create(
            session,
            scope="global",
            scope_id=0,
            name_ar="سيرفر",
            margin_percent=Decimal("50"),
        )
        product = type("P", (), {"price_usd": Decimal("3.00"), "cost_price_usd": Decimal("2.00")})()

        price = await StoreServerService.unit_price(product, server)

    assert price == Decimal("3.0000")


async def test_unit_price_keeps_product_price_when_no_cost():
    async with async_session_maker() as session:
        server = await StoreServerService.create(
            session,
            scope="global",
            scope_id=0,
            name_ar="سيرفر",
            margin_percent=Decimal("50"),
        )
        product = type("P", (), {"price_usd": Decimal("3.00"), "cost_price_usd": Decimal("0")})()

        price = await StoreServerService.unit_price(product, server)

    assert price == Decimal("3.0000")


async def test_admin_kb_opens_three_scopes():
    kb = admin_ssvc_scope_kb()
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "admin:ssvc_scope:category" in callbacks
    assert "admin:ssvc_scope:subcategory" in callbacks
    assert "admin:ssvc_scope:global" in callbacks
    # خدمة الأرقام لها نظام مخصص — لا تُعرض هنا كي لا يظهر سيرفر ميت.
    assert "admin:ssvc_scope:number_service" not in callbacks


async def test_number_server_margin_persists_and_shows_in_admin():
    async with async_session_maker() as session:
        svc = NumberService(
            code="wa_margin", name_ar="واتساب", emoji="💬", fivesim_code="wa", is_active=True
        )
        session.add(svc)
        await session.commit()
        await session.refresh(svc)
        server = await NumberServerService.create(
            session,
            number_service_id=svc.id,
            name_ar="سيرفر 5sim سريع",
            provider="fivesim",
            margin_percent=Decimal("35"),
        )
        loaded = await NumberServerService.get(session, server.id)
        kb = admin_nsvc_server_detail_kb(svc.id, loaded)
        callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]

    assert loaded.margin_percent == Decimal("35")
    assert f"admin:nsvc_server_edit_margin:{server.id}" in callbacks


async def _seed_section_with_products(session):
    """قسم رئيسي + قسم فرعي + قسم داخلي، مع منتجات في المستويين."""
    category = Category(name_ar="رشق", emoji="📈", type=CategoryType.SMM)
    session.add(category)
    await session.commit()
    await session.refresh(category)

    app = SubCategory(category_id=category.id, name_ar="انستقرام", emoji="📸")
    session.add(app)
    await session.commit()
    await session.refresh(app)

    inner = SubCategory(
        category_id=category.id, parent_sub_category_id=app.id,
        name_ar="متابعين", emoji="👥",
    )
    session.add(inner)
    await session.commit()
    await session.refresh(inner)

    prod1 = Product(
        sub_category_id=inner.id, name_ar="متابعين 1000", price_usd=Decimal("2"),
        cost_price_usd=Decimal("1"), status=ProductStatus.ACTIVE,
        fulfillment_type=ProductFulfillmentType.API,
    )
    prod2 = Product(
        sub_category_id=app.id, name_ar="باقة", price_usd=Decimal("5"),
        cost_price_usd=Decimal("1"), status=ProductStatus.ACTIVE,
        fulfillment_type=ProductFulfillmentType.API,
    )
    session.add_all([prod1, prod2])
    await session.commit()
    return category, app, inner


async def test_delete_products_for_category_keeps_subcategories():
    async with async_session_maker() as session:
        category, app, inner = await _seed_section_with_products(session)

        deleted, errors = await ProductService.delete_products_for_category(session, category.id)

        remaining_category = await session.get(Category, category.id)
        remaining_app = await session.get(SubCategory, app.id)
        remaining_inner = await session.get(SubCategory, inner.id)
        from sqlalchemy import select as _s

        products = list(
            (await session.execute(_s(Product).where(Product.sub_category_id.in_([app.id, inner.id])))).scalars().all()
        )

    assert deleted == 2
    assert errors == 0
    assert remaining_category is not None
    assert remaining_app is not None
    assert remaining_inner is not None
    assert products == []


async def test_delete_products_for_subcategory_keeps_subcategory():
    async with async_session_maker() as session:
        category, app, inner = await _seed_section_with_products(session)

        deleted, errors = await ProductService.delete_products_for_subcategory(session, app.id)

        remaining_app = await session.get(SubCategory, app.id)
        from sqlalchemy import select as _s

        products = list(
            (await session.execute(_s(Product).where(Product.sub_category_id.in_([app.id, inner.id])))).scalars().all()
        )

    assert deleted == 2
    assert errors == 0
    assert remaining_app is not None
    assert products == []
