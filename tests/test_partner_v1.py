"""Friend-panel (tlbkenne) protocol: pick one service into a section."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from database.engine import async_session_maker
from database.models import (
    ApiProvider,
    ApiProviderType,
    ApiProtocolType,
    Category,
    CategoryType,
    Product,
    ProductDisplayType,
    SubCategory,
)
from protocols.base import ProtocolInsufficientFundsError
from protocols.factory import ProtocolFactory
from protocols.partner_v1 import PartnerV1Protocol, is_partner_v1_config
from services.partner_catalog_service import PartnerCatalogService


def test_unwrap_maps_insufficient_balance():
    with pytest.raises(ProtocolInsufficientFundsError):
        PartnerV1Protocol.unwrap(
            {"ok": False, "error": "low", "code": "INSUFFICIENT_BALANCE"}
        )


def test_unwrap_returns_data():
    assert PartnerV1Protocol.unwrap({"ok": True, "data": {"balance": "3"}}) == {
        "balance": "3"
    }


def test_factory_uses_partner_engine_even_on_custom_type():
    protocol = ProtocolFactory.create(
        ApiProtocolType.CUSTOM,
        "http://169.58.216.253:8888/api/v1",
        "pak_test",
        custom_config={"engine": "partner_v1"},
    )
    assert isinstance(protocol, PartnerV1Protocol)
    assert is_partner_v1_config({"engine": "tlbkenne"}) is True
    assert is_partner_v1_config({"engine": "store_rest"}) is False


def test_parse_phone_service_has_no_link_or_quantity():
    protocol = PartnerV1Protocol("http://x/api/v1", "pak")
    svc = protocol._parse_service(
        {
            "service_id": 1001,
            "name": "Telegram Syria",
            "type": "tg",
            "category": "phone_number",
            "price": "0.25",
        }
    )
    assert svc is not None
    assert svc.external_id == "1001"
    assert svc.rate == Decimal("0.25")
    assert svc.requires_link is False
    assert svc.requires_quantity is False


def test_parse_smf_requires_link_and_quantity():
    protocol = PartnerV1Protocol("http://x/api/v1", "pak")
    svc = protocol._parse_service(
        {
            "service_id": 6100,
            "name": "IG Followers",
            "type": "smf",
            "category": "followers",
            "rate": "1.1",
            "min": 100,
            "max": 10000,
        }
    )
    assert svc is not None
    assert svc.requires_link is True
    assert svc.requires_quantity is True


@pytest.mark.asyncio
async def test_place_order_sends_service_id_only_for_numbers():
    protocol = PartnerV1Protocol("http://x/api/v1", "pak")
    captured = {}

    async def fake(method, path, payload=None, params=None):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        return {"order_id": 77, "status": "pending"}

    protocol._request = fake  # type: ignore[method-assign]
    order = await protocol.place_order("1001", "", 1)
    assert order.external_order_id == "77"
    assert captured["payload"] == {"service_id": 1001}


@pytest.mark.asyncio
async def test_place_order_sends_link_for_smm():
    protocol = PartnerV1Protocol("http://x/api/v1", "pak")
    captured = {}

    async def fake(method, path, payload=None, params=None):
        captured["payload"] = payload
        return {"order_id": 9, "status": "pending"}

    protocol._request = fake  # type: ignore[method-assign]
    await protocol.place_order("6100", "https://instagram.com/x", 500)
    assert captured["payload"]["link"] == "https://instagram.com/x"
    assert captured["payload"]["quantity"] == 500


@pytest.mark.asyncio
async def test_publish_one_creates_product_in_chosen_section_only():
    async with async_session_maker() as session:
        provider = ApiProvider(
            name="صديق",
            type=ApiProviderType.CUSTOM,
            protocol_type=ApiProtocolType.CUSTOM,
            api_url="http://example/api/v1",
            api_key="pak_test",
            custom_config='{"engine": "partner_v1"}',
            is_active=True,
        )
        category = Category(
            name_ar="أرقام", emoji="📞", type=CategoryType.NUMBERS, is_active=True
        )
        session.add_all([provider, category])
        await session.flush()
        sub = SubCategory(
            category_id=category.id, name_ar="تيليجرام", emoji="✈️", is_active=True
        )
        session.add(sub)
        await session.commit()
        await session.refresh(provider)
        await session.refresh(sub)

        proto = SimpleNamespace(
            external_id="1001",
            name="Telegram Syria",
            category="phone_number",
            service_type="tg",
            rate=Decimal("0.20"),
            min_quantity=1,
            max_quantity=1,
            description="SY",
            requires_link=False,
            requires_quantity=False,
            requires_player_id=False,
            supports_refill=False,
            supports_cancel=True,
            raw={"service_id": 1001},
        )
        product = await PartnerCatalogService.publish_one(
            session, provider, proto, sub.id, Decimal("0.80")
        )
        await session.commit()
        assert product.sub_category_id == sub.id
        assert product.provider_service_id == "1001"
        assert product.price_usd == Decimal("0.8000")
        assert product.cost_price_usd == Decimal("0.2000")
        assert product.requires_link is False
        assert product.requires_quantity is False
        assert product.display_type == ProductDisplayType.FIXED_TOTAL
        from sqlalchemy import select

        ours = list(
            (
                await session.execute(
                    select(Product).where(Product.provider_service_id == "1001")
                )
            ).scalars()
        )
        assert len(ours) == 1


def test_custom_preset_and_pick_button_exist():
    from keyboards.admin_providers_v2 import (
        CUSTOM_PROVIDER_CONFIG_PRESETS,
        custom_provider_presets_kb,
        provider_detail_kb,
    )

    assert CUSTOM_PROVIDER_CONFIG_PRESETS["tlbkenne"]["engine"] == "partner_v1"
    labels = [
        btn.text
        for row in custom_provider_presets_kb().inline_keyboard
        for btn in row
    ]
    assert any("صديق" in text for text in labels)

    provider = SimpleNamespace(
        id=3,
        is_active=True,
        total_services=0,
        type=SimpleNamespace(value="custom"),
    )
    texts = [
        btn.text
        for row in provider_detail_kb(provider).inline_keyboard
        for btn in row
    ]
    assert "🎯 اختيار خدمة واحدة لقسم" in texts
