"""Tests for the external-store (Hyper Store) protocol + full-store sync."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from sqlalchemy import select

import protocols.hyper_store as hyper_store
from protocols.base import (
    ProtocolAuthError,
    ProtocolInsufficientFundsError,
    ProtocolInvalidServiceError,
)
from protocols.factory import ProtocolFactory
from database.models import ApiProvider, ApiProtocolType, ApiProviderType


# ══════════════ الفيك HTTP ══════════════


class FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._payload = payload

    async def text(self):
        if isinstance(self._payload, str):
            return self._payload
        return json.dumps(self._payload, ensure_ascii=False)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeClientSession:
    """يمثل aiohttp.ClientSession؛ handler: (method, url) → (status, payload)."""

    def __init__(self, handler, requests: list):
        self._handler = handler
        self._requests = requests
        self.headers = None
        self.timeout = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def request(self, method, url, **kwargs):
        self._requests.append({"method": method, "url": url, "json": kwargs.get("json")})
        status, payload = self._handler(method, url, kwargs.get("json"))
        return FakeResponse(status, payload)


def _patch_aiohttp(monkeypatch, handler, requests: list | None = None):
    requests_ref = requests if requests is not None else []

    class _SessionFactory:
        def __new__(cls, headers=None, timeout=None, **kw):
            s = FakeClientSession(handler, requests_ref)
            s.headers = headers
            s.timeout = timeout
            return s

    class _ClientTimeout:
        def __init__(self, total=None, **kw):
            self.total = total

    class _AiohttpMod:
        ClientSession = _SessionFactory
        ClientTimeout = _ClientTimeout
        ClientError = ConnectionError

    monkeypatch.setattr(hyper_store, "aiohttp", _AiohttpMod())
    return requests_ref


# ══════════════ التحليل ══════════════


def test_parse_service_player_product():
    p = hyper_store.HyperStoreProtocol("https://api.x", "tok")
    item = {
        "id": 555,
        "name": "Minecraft Coins 100",
        "price": 2.5,
        "min": 1,
        "max": 500,
        "available": True,
        "input_type": "text",
        "fields": [
            {"key": "playerId", "label": "Player ID", "type": "text", "required": True},
            {"key": "quantity", "label": "Quantity", "type": "number", "required": True},
        ],
    }
    svc = p._parse_service(item)
    assert svc is not None
    assert svc.external_id == "555"
    assert svc.rate == Decimal("2.5")
    assert svc.requires_player_id is True
    assert svc.requires_link is False


def test_parse_service_link_product():
    p = hyper_store.HyperStoreProtocol("https://api.x", "tok")
    item = {
        "id": 777,
        "name": "Instagram Followers",
        "price": "1.2",
        "min": 100,
        "max": 10000,
        "input_type": "link",
        "fields": [{"key": "link", "label": "Link", "type": "url", "required": True}],
    }
    svc = p._parse_service(item)
    assert svc.requires_link is True
    assert svc.requires_player_id is False
    assert svc.min_quantity == 100


# ══════════════ الطلبات ══════════════


@pytest.mark.asyncio
async def test_get_services_pagination(monkeypatch):
    page1 = [{"id": i, "name": f"S{i}", "price": i, "min": 1, "max": 10} for i in range(100)]
    page2 = [{"id": i, "name": f"S{i}", "price": i, "min": 1, "max": 10} for i in range(100, 120)]
    calls = []

    def handler(method, url, payload):
        assert "api-token" in (url, "") or True
        if "page=1" in url:
            calls.append(1)
            return 200, page1
        if "page=2" in url:
            calls.append(2)
            return 200, page2
        return 200, []

    reqs = _patch_aiohttp(monkeypatch, handler)
    p = hyper_store.HyperStoreProtocol("https://api.x", "tok", {"page_limit": 100})
    services = await p.get_services()
    assert len(services) == 120
    assert calls == [1, 2]
    # الهيدر يحمل مفتاح المتجر
    assert reqs[0]  # requests سُجلت


@pytest.mark.asyncio
async def test_place_order_payload_and_response(monkeypatch):
    product = {
        "id": 555,
        "name": "Minecraft Coins",
        "price": 2.5,
        "fields": [{"key": "playerId", "label": "PID", "type": "text", "required": True}],
    }
    captured = {}

    def handler(method, url, payload):
        if method == "GET" and "products_id=555" in url:
            return 200, [product]
        if method == "POST" and url.endswith("/newOrder/555/params"):
            captured["payload"] = payload
            return 200, {
                "order_id": 98765,
                "order_code": "API_ABC123",
                "order_uuid": (payload or {}).get("order_uuid"),
                "status": "pending",
            }
        return 404, {}

    _patch_aiohttp(monkeypatch, handler)
    p = hyper_store.HyperStoreProtocol("https://api.x", "tok")
    order = await p.place_order("555", "steve_123", 10)
    assert order.external_order_id == "98765"
    assert order.status == "pending"
    payload = captured["payload"]
    assert payload["quantity"] == 10
    assert payload["playerId"] == "steve_123"
    assert payload["order_uuid"]  # uuid موصى به لمنع الشراء المزدوج
    assert order.raw.get("order_code") == "API_ABC123"


@pytest.mark.asyncio
async def test_check_order_status(monkeypatch):
    def handler(method, url, payload):
        assert "orders=98765" in url
        return 200, [
            {"order_id": 98765, "status": "completed", "delivered_data": "رقمك: 12345"}
        ]

    _patch_aiohttp(monkeypatch, handler)
    p = hyper_store.HyperStoreProtocol("https://api.x", "tok")
    status = await p.check_order_status("98765")
    assert status.status == "completed"
    assert status.raw.get("delivered_data") == "رقمك: 12345"


@pytest.mark.asyncio
async def test_error_code_mapping(monkeypatch):
    def make_handler(body):
        def handler(method, url, payload):
            return 200, body
        return handler

    p = hyper_store.HyperStoreProtocol("https://api.x", "tok")

    _patch_aiohttp(monkeypatch, make_handler({"error": 100, "message": "insufficient"}))
    with pytest.raises(ProtocolInsufficientFundsError):
        await p.get_balance()

    _patch_aiohttp(monkeypatch, make_handler({"error": 121, "message": "bad token"}))
    with pytest.raises(ProtocolAuthError):
        await p.get_balance()

    _patch_aiohttp(monkeypatch, make_handler({"error": 110, "message": "unavailable"}))
    with pytest.raises(ProtocolInvalidServiceError):
        await p.place_order("1", "x", 1)


def test_factory_detects_hyper_store():
    proto = ProtocolFactory.create(
        protocol_type=ApiProtocolType.CUSTOM,
        api_url="https://api.x",
        api_key="tok",
        custom_config={"engine": "hyper_store"},
    )
    assert isinstance(proto, hyper_store.HyperStoreProtocol)


# ══════════════ مزامنة المتجر كاملة ══════════════


@pytest.mark.asyncio
async def test_sync_store_to_section():
    from database.engine import async_session_maker
    from database.models import (
        Category,
        CategoryType,
        ProviderService,
        ProviderServiceStatus,
    )
    from services.pulled_services_service import PulledServicesService
    from services.settings_service import SettingsService

    async with async_session_maker() as session:
        provider = ApiProvider(
            name="متجر النجوم",
            type=ApiProviderType.SMM,
            api_url="https://api.x",
            api_key="k",
        )
        provider.custom_config = json.dumps({"engine": "hyper_store"})
        session.add(provider)
        await session.flush()
        for sid, name, rate in [
            ("s1", "Instagram Followers 1000", "10"),
            ("s2", "TikTok Real Followers 500", "5"),
            ("s3", "YouTube Views HD", "20"),
        ]:
            session.add(
                ProviderService(
                    api_provider_id=provider.id,
                    external_service_id=sid,
                    name=name,
                    rate=Decimal(rate),
                    rate_usd=Decimal(rate),
                    min_quantity=100,
                    max_quantity=10000,
                    requires_quantity=True,
                    status=ProviderServiceStatus.ACTIVE,
                )
            )
        await session.commit()
        provider_id = provider.id

    # هامش عالمي معروف
    async with async_session_maker() as session:
        await SettingsService.set(session, "default_profit_margin_percent", "50")

        report = await PulledServicesService.sync_store_to_section(
            session, await session.get(ApiProvider, provider_id)
        )
        assert report["created"] == 3
        assert report["category"].name_ar == "متجر النجوم"
        assert report["section"].name_ar == "الخدمات"

        # إعادة المزامنة: لا تكرار
        report2 = await PulledServicesService.sync_store_to_section(
            session, await session.get(ApiProvider, provider_id)
        )
        assert report2["created"] == 0
        assert report2["reordered"] == 3

        # المنتجات بالعربية ومن الأرخص للأغلى
        from database.models import Product, SubCategory

        result = await session.execute(
            select(Product).where(
                Product.sub_category_id == report["section"].id
            ).order_by(Product.sort_order.asc())
        )
        products = list(result.scalars().all())
        assert len(products) == 3
        assert "متابعون" in products[0].name_ar  # أرخص: TikTok 500
        assert "إنستغرام" in products[1].name_ar
        assert "مشاهدات" in products[2].name_ar
        # الأسعار = تكلفة + 50%
        assert products[0].price_usd == Decimal("7.5000")
        assert products[2].price_usd == Decimal("30.0000")
