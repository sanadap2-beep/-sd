"""
التسليم الفوري للاشتراكات الرقمية:
- تنسيق بيانات التسليم (LINK/كوبون/حساب READY_ACCOUNT) لرسائل تيليجرام
  وللواجهات النصية، مع تأمين HTML وعدم تسريب محتوى الحساب الحساس لأي جهة.
- غياب التسليم → لا شيء يُعرض.
- place_order في ggsoma: معرّف خارجي مستقر عند تمريره (لإعادة إرسال آمنة)،
  وترحيل requestId في أخطاء المزود.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import ApiProtocolType, ApiProvider, ApiProviderType, Product, ProductStatus, TransactionType, UnifiedOrder, UnifiedOrderStatus
from protocols.base import ProtocolInsufficientFundsError, ProtocolOrder
from protocols.ggsoma_v1 import GgsomaPartnerProtocol
from services.balance_service import BalanceService
from services.checkout_service import CheckoutService
from services.digital_delivery import format_delivery_html, format_delivery_text


def test_format_html_for_link_coupon_and_account():
    raw = {
        "ok": True,
        "delivery": {
            "link": "https://gg.example/redeem?x=1&y=2",
            "instructions": "فعّل خلال <7 أيام>",
        },
    }
    html = format_delivery_html(raw)
    assert "🔗 الرابط" in html
    assert "&amp;" in html  # مؤمَّن
    assert "<code>" in html


def test_format_html_for_ready_account_content_is_escaped():
    raw = {"delivery": {"content": "user@x.com\npass<&>word"}}
    html = format_delivery_html(raw)
    assert "user@x.com" in html
    assert "&lt;" in html and "&amp;" in html
    assert "🔑 بيانات الحساب" in html


def test_format_text_plain_variant():
    raw = {"delivery": {"code": "CAPCUT-1234-ABCD-5678", "instructions": "استخدمه مرة واحدة"}}
    text = format_delivery_text(raw)
    assert text is not None
    assert "CAPCUT-1234-ABCD-5678" in text
    assert "<code>" not in text  # نص عادي للواجهات


def test_no_delivery_returns_empty():
    assert format_delivery_html({"status": "COMPLETED", "ok": True}) == ""
    assert format_delivery_html(None) == ""
    assert format_delivery_text("{}") is None


def test_delivery_inside_nested_data_and_order():
    raw = {"ok": True, "data": {"delivery": {"link": "https://x"}}}
    assert "https://x" in format_delivery_html(raw)
    raw2 = {"ok": True, "order": {"delivery": {"link": "https://y"}}}
    assert "https://y" in format_delivery_html(raw2)


def test_legacy_sms_code_and_phone_shape_keeps_old_format():
    # كائنات الرسائل/الأرقام القديمة: code/phone على مستوى data — لا تُخلط
    # مع التوصيل الرقمي، ويبقى الرقم ظاهراً كما كان سابقاً.
    raw = {"ok": True, "data": {"code": "4466", "phone": "+963900000000"}}
    html = format_delivery_html(raw)
    assert "📞 الرقم" in html and "+963900000000" in html
    assert "🔑 الكود" in html and "4466" in html
    assert "🎟" not in html  # ليس مسار التوصيل الرقمي

    raw_top = {"sms": "1234"}
    assert "🔑 الكود" in format_delivery_html(raw_top) and "1234" in format_delivery_html(raw_top)


@pytest.mark.asyncio
async def test_place_order_keeps_stable_external_id_when_provided():
    protocol = GgsomaPartnerProtocol("http://x/api/v1", "sk_test")
    captured = {}

    async def fake(method, path, payload=None, params=None):
        captured["payload"] = payload
        return {
            "ok": True,
            "orderCode": "SO-20260903-AAAA",
            "status": "COMPLETED",
            "totalCharged": "1.00",
        }

    protocol._request = fake  # type: ignore[method-assign]
    order = await protocol.place_order(
        "gemini-pro-monthly",
        quantity=1,
        extra_params={"externalOrderId": "shop-1001"},
    )
    assert order.external_order_id == "SO-20260903-AAAA"
    assert captured["payload"]["externalOrderId"] == "shop-1001"
    assert captured["payload"]["productSlug"] == "gemini-pro-monthly"
    assert captured["payload"]["quantity"] == 1

    # إعادة إرسال بنفس المعرّف → نفس الطلب (idempotent per docs).
    order2 = await protocol.place_order(
        "gemini-pro-monthly",
        quantity=1,
        extra_params={"externalOrderId": "shop-1001"},
    )
    assert order2.external_order_id == "SO-20260903-AAAA"


@pytest.mark.asyncio
async def test_error_maps_raise_with_request_id_and_insufficient_funds():
    with pytest.raises(ProtocolInsufficientFundsError) as excinfo:
        GgsomaPartnerProtocol.unwrap(
            {
                "ok": False,
                "error": {
                    "code": "INSUFFICIENT_BALANCE",
                    "message": "رصيد غير كافٍ",
                    "requestId": "req_abc123",
                },
            }
        )
    assert "req_abc123" in str(excinfo.value)
    assert excinfo.value.request_id == "req_abc123"


async def _seed_auto_product(session, monkeypatch):
    """مزود ggsoma نشط + كتالوج بمنتج واحد منشور عبر المزامنة."""
    from database.models import Category, CategoryType
    from services.subscriptions_sync_service import SubscriptionsSyncService

    provider = ApiProvider(
        name="ggsoma",
        type=ApiProviderType.SUBSCRIPTIONS,
        protocol_type=ApiProtocolType.CUSTOM,
        api_url="https://ggsoma.store/api/partner/v1",
        api_key="sk_live_test123",
        custom_config=json.dumps({"engine": "ggsoma"}),
        is_active=True,
    )
    session.add(provider)
    await session.commit()
    await session.refresh(provider)

    product_card = {
        "id": 42,
        "slug": "gemini-pro-monthly",
        "productCode": "G-42",
        "name": "Gemini Pro — 30 days",
        "provider": {
            "id": 3,
            "key": "gemini",
            "name": "Gemini",
            "emoji": {"normal": "✨"},
        },
        "deliveryType": "LINK",
        "sortOrder": 1,
        "catalogPrice": "12.50",
        "yourPrice": "12.50",
        "currency": "USD",
        "durationDays": 30,
        "stock": {"inStock": True, "count": 48, "maxQuantity": 48},
        "flags": {"instantDelivery": True},
    }

    async def fake_services(self, service_type=None, category=None):
        return [GgsomaPartnerProtocol("x", "y")._parse_product(product_card)]

    async def fake_providers(self):
        return [{"key": "gemini", "name": "Gemini", "emoji": {"normal": "✨"}}]

    monkeypatch.setattr(GgsomaPartnerProtocol, "get_services", fake_services)
    monkeypatch.setattr(GgsomaPartnerProtocol, "get_providers", fake_providers)
    report = await SubscriptionsSyncService.sync_provider(session, provider)
    assert report["products_created"] == 1

    result = await session.execute(select(Product))
    return provider, result.scalars().all()[0]


@pytest.mark.asyncio
async def test_checkout_completes_instantly_with_delivery(monkeypatch):
    async with async_session_maker() as session:
        provider, product = await _seed_auto_product(session, monkeypatch)

        from database.models import User

        user = User(telegram_id=88001, username="buyer", full_name="Buyer")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        await BalanceService.add_balance(
            session,
            user.id,
            Decimal("20"),
            TransactionType.DEPOSIT,
            payment_reference="fund-88001",
        )

        # المزود يسلّم فوراً: COMPLETED + delivery في نفس الرد.
        async def fake_place(self, service_id, target="", quantity=1, extra_params=None):
            return ProtocolOrder(
                external_order_id="SO-20260903-GG1",
                status="completed",
                charge=Decimal("12.50"),
                raw={
                    "ok": True,
                    "orderCode": "SO-20260903-GG1",
                    "status": "COMPLETED",
                    "deliveryType": "LINK",
                    "quantity": 1,
                    "totalCharged": "12.50",
                    "currency": "USD",
                    "delivery": {
                        "link": "https://gemini.example/redeem/XYZ",
                        "instructions": "فعّل خلال 7 أيام",
                    },
                },
            )

        monkeypatch.setattr(GgsomaPartnerProtocol, "place_order", fake_place)

        checkout = await CheckoutService.purchase(session, user.id, product.id)

        order = checkout.order
        assert order.status == UnifiedOrderStatus.COMPLETED
        assert order.completed_at is not None
        assert order.external_order_id == "SO-20260903-GG1"
        assert order.result_data and "gemini.example" in order.result_data
        assert checkout.delivery_value is not None
        assert "الرابط: https://gemini.example/redeem/XYZ" in checkout.delivery_value
        assert "فعّل خلال 7 أيام" in checkout.delivery_value
        # السعر = 12.50 × 1.23 = 15.38
        assert order.price_usd == Decimal("15.38")

        # الرصيد خُصم مرة واحدة فقط.
        await session.refresh(user)
        assert user.balance == Decimal("20.0000") - Decimal("15.3800")

        # لا يُعيده المراقب: ليس pending/processing.
        from database.models import UnifiedOrder as UO

        rows = list(
            (
                await session.execute(
                    select(UO).where(UO.status.in_(["pending", "processing"]))
                )
            )
            .scalars()
            .all()
        )
        assert order.id not in {r.id for r in rows}


@pytest.mark.asyncio
async def test_checkout_keeps_processing_when_provider_is_not_instant(monkeypatch):
    async with async_session_maker() as session:
        provider, product = await _seed_auto_product(session, monkeypatch)

        from database.models import User

        user = User(telegram_id=88002, username="buyer2", full_name="Buyer 2")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        await BalanceService.add_balance(
            session,
            user.id,
            Decimal("20"),
            TransactionType.DEPOSIT,
            payment_reference="fund-88002",
        )

        async def fake_place(self, service_id, target="", quantity=1, extra_params=None):
            return ProtocolOrder(
                external_order_id="SO-20260903-GG2",
                status="processing",
                charge=Decimal("12.50"),
                raw={"ok": True, "status": "PROCESSING", "orderCode": "SO-20260903-GG2"},
            )

        monkeypatch.setattr(GgsomaPartnerProtocol, "place_order", fake_place)

        checkout = await CheckoutService.purchase(session, user.id, product.id)
        assert checkout.order.status == UnifiedOrderStatus.PROCESSING
        assert checkout.delivery_value is None
        assert checkout.order.result_data is None
