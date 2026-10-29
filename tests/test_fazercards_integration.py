"""اختبارات تكامل FazerCards بلا شبكة أو مفتاح حي.

تغطي العقد الرسمي: catalog الهرمي، حقول اللاعب الديناميكية، idempotency،
حالة الطلب، ثم نشر الكتالوج في قسم ألعاب مستقل.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

import protocols.fazercards as fazercards
from database.engine import async_session_maker
from database.models import (
    ApiProtocolType,
    ApiProvider,
    ApiProviderType,
    Product,
    ProviderPriceType,
    ProviderService,
    ProviderServiceStatus,
    SubCategory,
)
from protocols.base import ProtocolAuthError, ProtocolInsufficientFundsError
from protocols.factory import ProtocolFactory
from services.provider_input_service import encode_provider_fields
from services.pulled_services_service import PulledServicesService


class FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self.payload = payload

    async def text(self):
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, handler, captured, headers=None, timeout=None, **_kwargs):
        self.handler = handler
        self.captured = captured
        self.headers = headers or {}
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def request(self, method, url, **kwargs):
        entry = {
            "method": method,
            "url": url,
            "params": kwargs.get("params"),
            "json": kwargs.get("json"),
            "headers": dict(self.headers),
        }
        self.captured.append(entry)
        status, payload = self.handler(method, url, kwargs)
        return FakeResponse(status, payload)


def patch_aiohttp(monkeypatch, handler):
    captured = []

    class SessionFactory:
        def __new__(cls, headers=None, timeout=None, **kwargs):
            return FakeSession(handler, captured, headers=headers, timeout=timeout, **kwargs)

    class Timeout:
        def __init__(self, total=None, **_kwargs):
            self.total = total

    class FakeAiohttp:
        ClientSession = SessionFactory
        ClientTimeout = Timeout
        ClientError = ConnectionError

    monkeypatch.setattr(fazercards, "aiohttp", FakeAiohttp())
    return captured


@pytest.mark.asyncio
async def test_catalog_maps_offers_fields_and_pagination(monkeypatch):
    def handler(method, url, kwargs):
        assert method == "GET"
        if url.endswith("/topups"):
            cursor = (kwargs.get("params") or {}).get("cursor")
            if cursor is None:
                return 200, {
                    "ok": True,
                    "items": [{"category_id": "pubg_mobile_auto", "name": "PUBG Mobile"}],
                    "meta": {"next_cursor": "next-page"},
                }
            assert cursor == "next-page"
            return 200, {
                "ok": True,
                "items": [{"category_id": "mobile_legends", "name": "Mobile Legends"}],
                "meta": {},
            }
        if url.endswith("/topups/offers"):
            category = (kwargs.get("params") or {}).get("category_id")
            if category == "pubg_mobile_auto":
                return 200, {
                    "ok": True,
                    "category_id": category,
                    "name": "PUBG Mobile",
                    "fields": [{"key": "player_id", "label": "Player ID", "type": "text"}],
                    "offers": [{"offer_id": "60_uc", "name": "60 UC", "price_usd": "0.91"}],
                }
            return 200, {
                "ok": True,
                "category_id": category,
                "name": "Mobile Legends",
                "fields": [
                    {"key": "player_id", "label": "Player ID", "type": "text"},
                    {"key": "zone_id", "label": "Zone ID", "type": "text"},
                ],
                "offers": [{"offer_id": "86_diamonds", "name": "86 Diamonds", "price_usd": "1.25"}],
            }
        raise AssertionError(f"unexpected {url}")

    captured = patch_aiohttp(monkeypatch, handler)
    protocol = fazercards.FazerCardsProtocol(
        "https://api.fzr.cards/api/v2", "fc_test", {"catalog_concurrency": 1}
    )
    services = await protocol.get_services()

    assert [service.external_id for service in services] == [
        "pubg_mobile_auto::60_uc",
        "mobile_legends::86_diamonds",
    ]
    assert services[0].rate == Decimal("0.91")
    assert services[0].requires_player_id is True
    assert services[0].requires_quantity is False
    assert services[0].raw["fields"][0]["label_ar"] == "معرّف اللاعب"
    assert services[1].raw["fields"][1]["label_ar"] == "معرّف المنطقة / السيرفر"
    assert len([row for row in captured if row["url"].endswith("/topups")]) == 2


@pytest.mark.asyncio
async def test_order_sends_structured_fields_idempotency_and_reads_status(monkeypatch):
    target = encode_provider_fields({"player_id": "123456", "zone_id": "2214"})

    def handler(method, url, kwargs):
        if method == "POST" and url.endswith("/topups/order"):
            assert kwargs["json"] == {
                "category_id": "mobile_legends",
                "offer_id": "86_diamonds",
                "fields": {"player_id": "123456", "zone_id": "2214"},
            }
            return 201, {
                "ok": True,
                "order": {"id": "ord-42", "status": "processing", "total_usd": "1.25"},
            }
        if method == "GET" and url.endswith("/orders/ord-42"):
            return 200, {
                "ok": True,
                "order": {"id": "ord-42", "status": "completed", "total_usd": "1.25"},
            }
        raise AssertionError(f"unexpected {method} {url}")

    captured = patch_aiohttp(monkeypatch, handler)
    protocol = fazercards.FazerCardsProtocol("https://api.fzr.cards/api/v2", "fc_test")
    order = await protocol.place_order("mobile_legends::86_diamonds", target, 1)
    status = await protocol.check_order_status("ord-42")

    assert order.external_order_id == "ord-42"
    assert order.status == "processing"
    assert order.charge == Decimal("1.25")
    assert status.status == "completed"
    write = captured[0]
    assert write["headers"]["X-API-Key"] == "fc_test"
    assert write["headers"].get("Idempotency-Key")


@pytest.mark.asyncio
async def test_order_accepts_stable_idempotency_key_from_checkout(monkeypatch):
    def handler(method, url, _kwargs):
        assert method == "POST" and url.endswith("/topups/order")
        return 201, {"ok": True, "order": {"id": "ord-stable", "status": "created"}}

    captured = patch_aiohttp(monkeypatch, handler)
    protocol = fazercards.FazerCardsProtocol("https://api.fzr.cards/api/v2", "fc_test")
    order = await protocol.place_order(
        "pubg_mobile_auto::60_uc",
        encode_provider_fields({"player_id": "123456"}),
        1,
        extra_params={"_idempotency_key": "bot-stable-purchase-42"},
    )

    assert order.raw["idempotency_key"] == "bot-stable-purchase-42"
    assert captured[0]["headers"]["Idempotency-Key"] == "bot-stable-purchase-42"
    assert "_idempotency_key" not in captured[0]["json"]["fields"]


@pytest.mark.asyncio
async def test_idempotent_order_retries_with_the_same_key(monkeypatch):
    attempts = 0

    def handler(method, url, _kwargs):
        nonlocal attempts
        assert method == "POST" and url.endswith("/topups/order")
        attempts += 1
        if attempts == 1:
            raise ConnectionError("temporary network error")
        return 201, {"ok": True, "order": {"id": "ord-retry", "status": "created"}}

    captured = patch_aiohttp(monkeypatch, handler)
    protocol = fazercards.FazerCardsProtocol(
        "https://api.fzr.cards/api/v2", "fc_test", {"max_retries": 1}
    )
    order = await protocol.place_order(
        "pubg_mobile_auto::60_uc", encode_provider_fields({"player_id": "123456"}), 1
    )

    assert order.external_order_id == "ord-retry"
    assert attempts == 2
    assert captured[0]["headers"]["Idempotency-Key"] == captured[1]["headers"]["Idempotency-Key"]


@pytest.mark.asyncio
async def test_plain_single_player_id_resolves_catalog_field(monkeypatch):
    def handler(method, url, kwargs):
        if method == "GET" and url.endswith("/topups/offers"):
            return 200, {
                "ok": True,
                "fields": [{"key": "uid", "label": "UID", "type": "text"}],
                "offers": [],
            }
        if method == "POST" and url.endswith("/topups/order"):
            assert kwargs["json"]["fields"] == {"uid": "991122"}
            return 200, {"ok": True, "order": {"id": "ord-1", "status": "created"}}
        raise AssertionError(f"unexpected {method} {url}")

    patch_aiohttp(monkeypatch, handler)
    protocol = fazercards.FazerCardsProtocol("https://api.fzr.cards/api/v2", "fc_test")
    order = await protocol.place_order("pubg_mobile_auto::60_uc", "991122", 1)
    assert order.status == "pending"


@pytest.mark.asyncio
async def test_errors_are_mapped_for_admin_and_purchase_flow(monkeypatch):
    patch_aiohttp(monkeypatch, lambda *_args: (401, {"message": "bad API key"}))
    protocol = fazercards.FazerCardsProtocol("https://api.fzr.cards/api/v2", "bad")
    with pytest.raises(ProtocolAuthError):
        await protocol.get_balance()

    patch_aiohttp(monkeypatch, lambda *_args: (400, {"message": "Insufficient balance"}))
    with pytest.raises(ProtocolInsufficientFundsError):
        await protocol.place_order("pubg_mobile_auto::60_uc", "123456", 1)


def test_factory_detects_fazercards_engine():
    protocol = ProtocolFactory.create(
        ApiProtocolType.CUSTOM,
        "https://api.fzr.cards/api/v2",
        "fc_test",
        {"engine": "fazercards"},
    )
    assert isinstance(protocol, fazercards.FazerCardsProtocol)


async def _seed_fazercards_provider() -> tuple[int, dict[str, int]]:
    async with async_session_maker() as session:
        provider = ApiProvider(
            name="FazerCards",
            type=ApiProviderType.GAMES,
            protocol_type=ApiProtocolType.CUSTOM,
            api_url="https://api.fzr.cards/api/v2",
            api_key="fc_test",
            currency="USD",
            rate_to_usd=Decimal("1"),
            custom_config=json.dumps({"engine": "fazercards"}),
            is_active=True,
        )
        session.add(provider)
        await session.flush()
        service_ids = {}
        for external_id, name, category, rate, fields in (
            (
                "pubg_mobile_auto::60_uc",
                "PUBG Mobile — 60 UC",
                "PUBG Mobile",
                "0.90",
                [{"key": "player_id", "label": "Player ID", "label_ar": "معرّف اللاعب"}],
            ),
            (
                "mobile_legends::86_diamonds",
                "Mobile Legends — 86 Diamonds",
                "Mobile Legends",
                "1.20",
                [
                    {"key": "player_id", "label": "Player ID", "label_ar": "معرّف اللاعب"},
                    {"key": "zone_id", "label": "Zone ID", "label_ar": "معرّف المنطقة / السيرفر"},
                ],
            ),
        ):
            service = ProviderService(
                api_provider_id=provider.id,
                external_service_id=external_id,
                name=name,
                category=category,
                service_type="game_topup",
                rate=Decimal(rate),
                rate_usd=Decimal(rate),
                price_type=ProviderPriceType.PER_ITEM,
                min_quantity=1,
                max_quantity=1,
                requires_link=False,
                requires_quantity=False,
                requires_player_id=True,
                status=ProviderServiceStatus.ACTIVE,
                raw_data=json.dumps(
                    {
                        "engine": "fazercards",
                        "family": "topups",
                        "category_id": category.lower().replace(" ", "_"),
                        "offer_id": external_id.split("::", 1)[1],
                        "fields": fields,
                    }
                ),
            )
            session.add(service)
            await session.flush()
            service_ids[external_id] = service.id
        await session.commit()
        return provider.id, service_ids


@pytest.mark.asyncio
async def test_sync_creates_one_game_section_per_fazercards_category():
    provider_id, service_ids = await _seed_fazercards_provider()

    async with async_session_maker() as session:
        provider = await session.get(ApiProvider, provider_id)
        report = await PulledServicesService.sync_fazercards_games_section(
            session, provider, Decimal("10")
        )
        category = report["category"]
        sections = list(
            (
                await session.execute(select(SubCategory).where(SubCategory.category_id == category.id))
            ).scalars().all()
        )
        products = list(
            (
                await session.execute(
                    select(Product).where(Product.api_provider_id == provider_id).order_by(Product.id)
                )
            ).scalars().all()
        )

        assert category.name_ar == "شحن الألعاب — FazerCards"
        assert category.type.value == "games"
        assert {section.name_ar for section in sections} == {"ببجي موبايل", "موبايل ليجندز"}
        assert report["products_created"] == 2
        assert {product.provider_service_ref_id for product in products} == set(service_ids.values())
        assert all(product.is_auto_published for product in products)
        assert all(product.requires_quantity is False for product in products)
        assert Decimal(str(products[1].price_usd)) == Decimal("1.3200")

        # إعادة النشر تحدّث السعر نفسه ولا تصنع منتجات مضاعفة.
        service = await session.get(ProviderService, service_ids["pubg_mobile_auto::60_uc"])
        service.rate_usd = Decimal("1.00")
        await session.commit()
        rerun = await PulledServicesService.sync_fazercards_games_section(
            session, provider, Decimal("10")
        )
        products_after = list(
            (await session.execute(select(Product).where(Product.api_provider_id == provider_id))).scalars().all()
        )

    assert rerun["products_created"] == 0
    assert rerun["products_updated"] == 2
    assert len(products_after) == 2
    pubg_product = next(product for product in products_after if product.provider_service_ref_id == service_ids["pubg_mobile_auto::60_uc"])
    assert Decimal(str(pubg_product.price_usd)) == Decimal("1.1000")


class _FakeState:
    def __init__(self, data: dict):
        self.data = dict(data)
        self.state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.data.clear()
        self.state = None


class _FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.answers: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None, **_kwargs):
        self.answers.append((text, reply_markup))


@pytest.mark.asyncio
async def test_bot_collects_player_and_zone_before_fazercards_order():
    """عرض MLBB متعدد الحقول لا يصل إلى التأكيد قبل إدخال Zone ID أيضاً."""
    from handlers.games import fazercards_field_received
    from services.provider_input_service import decode_provider_fields

    provider_id, service_ids = await _seed_fazercards_provider()
    async with async_session_maker() as session:
        provider = await session.get(ApiProvider, provider_id)
        await PulledServicesService.sync_fazercards_games_section(session, provider)
        product = (
            await session.execute(
                select(Product).where(
                    Product.provider_service_ref_id == service_ids["mobile_legends::86_diamonds"]
                )
            )
        ).scalar_one()
        service = await session.get(
            ProviderService, service_ids["mobile_legends::86_diamonds"]
        )
        fields = json.loads(service.raw_data)["fields"]
        state = _FakeState(
            {
                "product_id": product.id,
                "fazercards_fields": fields,
                "fazercards_values": {},
                "fazercards_field_index": 0,
            }
        )

        player = _FakeMessage("123456")
        await fazercards_field_received(player, state, session, object())
        assert state.data["fazercards_values"] == {"player_id": "123456"}
        assert state.data["fazercards_field_index"] == 1
        assert "المنطقة" in player.answers[-1][0]

        zone = _FakeMessage("2214")
        await fazercards_field_received(zone, state, session, object())

    assert decode_provider_fields(state.data["target"]) == {
        "player_id": "123456",
        "zone_id": "2214",
    }
    assert any("موبايل ليجندز" in text for text, _markup in zone.answers)


@pytest.mark.asyncio
async def test_single_required_fazercards_field_uses_structured_field_flow():
    """حتى العرض ذو UID واحد يحفظه بحقل مزود صريح، لا كهدف مبهم."""
    from handlers.games import _start_fazercards_fields
    from states.states import GamesOrderStates

    provider_id, service_ids = await _seed_fazercards_provider()
    async with async_session_maker() as session:
        provider = await session.get(ApiProvider, provider_id)
        await PulledServicesService.sync_fazercards_games_section(session, provider)
        product = (
            await session.execute(
                select(Product).where(
                    Product.provider_service_ref_id == service_ids["pubg_mobile_auto::60_uc"]
                )
            )
        ).scalar_one()
        message = _FakeMessage("")
        state = _FakeState({})
        started = await _start_fazercards_fields(message, state, product, session)

    assert started is True
    assert state.state == GamesOrderStates.waiting_provider_fields
    assert state.data["fazercards_fields"][0]["key"] == "player_id"


@pytest.mark.asyncio
async def test_provider_specific_games_margin_is_shown_and_used():
    from handlers.admin.api_providers import _fazercards_games_margin
    from keyboards.admin_providers_v2 import provider_detail_kb

    async with async_session_maker() as session:
        provider = ApiProvider(
            name="Fazer Margin",
            type=ApiProviderType.GAMES,
            protocol_type=ApiProtocolType.CUSTOM,
            api_url="https://api.fzr.cards/api/v2",
            api_key="fc_test",
            currency="USD",
            rate_to_usd=Decimal("1"),
            custom_config=json.dumps(
                {"engine": "fazercards", "games_margin_percent": "17.5"}
            ),
            is_active=True,
        )
        session.add(provider)
        await session.flush()
        margin = await _fazercards_games_margin(provider)
        buttons = [
            button
            for row in provider_detail_kb(provider).inline_keyboard
            for button in row
        ]

    assert margin == Decimal("17.5")
    publish = next(button for button in buttons if "نشر كتالوج الألعاب" in button.text)
    change = next(button for button in buttons if "تعديل هامش كتالوج" in button.text)
    assert "%17.5" in publish.text
    assert change.callback_data == f"admin:aprov_fz_games_margin:{provider.id}"


@pytest.mark.asyncio
async def test_admin_margin_input_is_saved_per_fazercards_provider():
    from handlers.admin.api_providers import aprov_fazercards_games_margin_received

    async with async_session_maker() as session:
        provider = ApiProvider(
            name="Fazer Settings",
            type=ApiProviderType.GAMES,
            protocol_type=ApiProtocolType.CUSTOM,
            api_url="https://api.fzr.cards/api/v2",
            api_key="fc_test",
            currency="USD",
            rate_to_usd=Decimal("1"),
            custom_config=json.dumps({"engine": "fazercards"}),
            is_active=True,
        )
        session.add(provider)
        await session.commit()
        await session.refresh(provider)
        state = _FakeState({"fazercards_margin_provider_id": provider.id})
        message = _FakeMessage("12.5")
        await aprov_fazercards_games_margin_received(message, state, session)
        await session.refresh(provider)

        saved = json.loads(provider.custom_config)

    assert saved["games_margin_percent"] == "12.5"
    assert state.data == {}
    assert any("12.5%" in text for text, _markup in message.answers)


@pytest.mark.asyncio
async def test_checkout_passes_structured_fazercards_target_to_protocol(monkeypatch):
    """مسار Checkout العام لا يحوّل JSON الحقول إلى سلسلة Player ID مبهمة."""
    from database.models import User
    from protocols.base import ProtocolOrder
    from services.checkout_service import CheckoutService

    provider_id, service_ids = await _seed_fazercards_provider()
    async with async_session_maker() as session:
        provider = await session.get(ApiProvider, provider_id)
        await PulledServicesService.sync_fazercards_games_section(session, provider)
        product = (
            await session.execute(
                select(Product).where(
                    Product.provider_service_ref_id == service_ids["mobile_legends::86_diamonds"]
                )
            )
        ).scalar_one()
        user = User(
            telegram_id=977001,
            username="checkout_fazer",
            full_name="Checkout Fazer",
            balance=Decimal("20"),
            is_activated=True,
        )
        session.add(user)
        await session.commit()
        target = encode_provider_fields({"player_id": "123456", "zone_id": "2214"})
        captured: dict[str, object] = {}

        class _CheckoutProtocol:
            async def place_order(self, service_id, target, quantity, extra_params=None):
                captured.update(
                    service_id=service_id,
                    target=target,
                    quantity=quantity,
                    extra_params=extra_params,
                )
                return ProtocolOrder(
                    external_order_id="checkout-fz-1",
                    status="processing",
                    charge=Decimal("1.20"),
                    raw={},
                )

        monkeypatch.setattr(
            ProtocolFactory,
            "create_from_provider",
            staticmethod(lambda _provider: _CheckoutProtocol()),
        )
        result = await CheckoutService.purchase(
            session, user.id, product.id, target=target, quantity=1
        )

    assert captured["service_id"] == "mobile_legends::86_diamonds"
    assert captured["target"] == target
    assert result.order.target == target
    assert result.order.external_order_id == "checkout-fz-1"


class _FakeEditMessage(_FakeMessage):
    async def edit_text(self, text, reply_markup=None, **_kwargs):
        self.answers.append((text, reply_markup))


class _FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeEditMessage("")
        self.answered = []

    async def answer(self, text=None, **_kwargs):
        self.answered.append(text)


@pytest.mark.asyncio
async def test_admin_service_view_labels_fazercards_cost_per_package():
    from handlers.admin.api_providers import aprov_service_view

    provider_id, service_ids = await _seed_fazercards_provider()
    callback = _FakeCallback(
        f"admin:aprov_svc:{service_ids['pubg_mobile_auto::60_uc']}"
    )
    async with async_session_maker() as session:
        await aprov_service_view(callback, session)

    text = callback.message.answers[-1][0]
    assert "للباقة / القطعة" in text
    assert "/ 1000" not in text


@pytest.mark.asyncio
async def test_same_named_fazercards_providers_get_independent_game_sections():
    """لا يخلط النشر الآلي عروض حسابي API يحملان اسم عرض واحداً."""
    first_id, _first_services = await _seed_fazercards_provider()
    second_id, _second_services = await _seed_fazercards_provider()

    async with async_session_maker() as session:
        first = await session.get(ApiProvider, first_id)
        second = await session.get(ApiProvider, second_id)
        first_report = await PulledServicesService.sync_fazercards_games_section(session, first)
        second_report = await PulledServicesService.sync_fazercards_games_section(session, second)

    assert first_report["category"].id != second_report["category"].id
    assert first_report["category"].name_ar == "شحن الألعاب — FazerCards"
    assert second_report["category"].name_ar.endswith(f"#{second_id}")
