"""إصلاحات: تطبيقات وأكواد جاهزة + تفاصيل الخدمة كأزرار.

يغطي:
1) شراء عنصر جاهز لم يعد يستعمل product_id=0 (قيد FOREIGN KEY).
   الطلب يُعرض بـ product_id NULL بدل رقم وهمي.
2) المحتوى (التعليمات/الرابط) لا يظهر قبل الشراء، ويظهر بعده فقط.
3) تفاصيل خدمة SMM تُرسم كأزرار Inline (صفّان صفّان) بدل نص عادي.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    ApiProvider,
    ApiProviderType,
    Category,
    CategoryType,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    ProviderService,
    ReadyCodeItem,
    SubCategory,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from handlers.ready_codes import ready_code_view, ready_code_buy
from keyboards.ready_codes import (
    ready_code_detail_kb,
    admin_ready_code_edit_kb,
)
from services.margin_service import MarginService
from services.referral_guard_service import ReferralGuardService
from services.smm_price_service import format_details_kb, _fmt_price


class DummyMessage:
    def __init__(self):
        self.answers = []
        self.edits = []

    async def answer(self, text=None, reply_markup=None, **kwargs):
        self.answers.append((text, reply_markup))
        return SimpleNamespace(chat=SimpleNamespace(id=1), message_id=1)

    async def edit_text(self, text=None, reply_markup=None, **kwargs):
        self.edits.append((text, reply_markup))
        return None


class DummyCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = DummyMessage()
        self.alerts = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.alerts.append((text, show_alert))


async def _seed_user(session, telegram_id=6707747395, balance="100"):
    user = User(
        telegram_id=telegram_id,
        username="test",
        full_name="Test User",
        language_code="ar",
        balance=Decimal(balance),
        is_activated=True,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.mark.asyncio
async def test_ready_code_view_hides_content_before_purchase():
    async with async_session_maker() as session:
        item = ReadyCodeItem(
            name_ar="تطبيق جاهز",
            description="وصف مجاني",
            price_usd=Decimal("5.00"),
            instructions="الخطوة 1، الخطوة 2",
            file_url="https://example.com/app.apk",
            is_active=True,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)

        cb = DummyCallback(f"readycode:view:{item.id}")
        user = await _seed_user(session)
        await ready_code_view(cb, session, user)

        text = cb.message.edits[-1][0]
        # لا يظهر المحتوى في التفاصيل قبل الشراء.
        assert "app.apk" not in text
        assert "الخطوة 1" not in text
        assert "🔒" in text
        kb = cb.message.edits[-1][1]
        assert kb is not None


@pytest.mark.asyncio
async def test_ready_code_buy_creates_order_with_null_product_and_delivers_content():
    async with async_session_maker() as session:
        item = ReadyCodeItem(
            name_ar="تطبيق جاهز",
            description="وصف مجاني",
            price_usd=Decimal("5.00"),
            instructions="الخطوة 1، الخطوة 2",
            file_url="https://example.com/app.apk",
            is_active=True,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)

        user = await _seed_user(session, balance="50")
        cb = DummyCallback(f"readycode:buy:{item.id}")
        await ready_code_buy(cb, session, user, bot=None)

        # الطلب أُنشئ بدون منتج وهمي — product_id NULL (كان 0 فيكسر القيد).
        orders = list(
            (await session.execute(select(UnifiedOrder).where(UnifiedOrder.user_id == user.id))).scalars().all()
        )
        assert len(orders) == 1
        assert orders[0].product_id is None
        assert orders[0].status == UnifiedOrderStatus.COMPLETED

        # بعد الشراء يظهر المحتوى.
        text = cb.message.edits[-1][0]
        assert "app.apk" in text
        assert "الخطوة 1" in text


@pytest.mark.asyncio
async def test_ready_code_buy_free_item_no_balance_needed():
    async with async_session_maker() as session:
        item = ReadyCodeItem(
            name_ar="كود مجاني",
            description="مجاني",
            price_usd=Decimal("0"),
            instructions="استخدم كذا",
            file_url="",
            is_active=True,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)

        user = await _seed_user(session, balance="0")
        cb = DummyCallback(f"readycode:buy:{item.id}")
        await ready_code_buy(cb, session, user, bot=None)

        orders = list(
            (await session.execute(select(UnifiedOrder).where(UnifiedOrder.user_id == user.id))).scalars().all()
        )
        assert len(orders) == 1
        assert orders[0].price_usd == Decimal("0")
        assert "استخدم كذا" in cb.message.edits[-1][0]


@pytest.mark.asyncio
async def test_smm_details_rendered_as_inline_keyboard():
    details = {
        "type": "رشق متابعين",
        "name": "Followers",
        "min": 100,
        "max": 1000000,
        "refill": True,
        "quality": "سريعة",
        "drop_rate": "0%",
        "speed": "100k",
        "estimated_time": "خلال 37 دقيقة",
    }
    kb = format_details_kb(details, Decimal("0.04"), "subcat:3")
    # 9 صفوف تفاصيل (صفّان صفّان) + صف الرجوع.
    assert len(kb.inline_keyboard) == 10
    # كل صف من الصفوف الأولى فيه زرّان (القيمة + العنوان).
    for row in kb.inline_keyboard[:9]:
        assert len(row) == 2
    # آخر صف: زر الرجوع.
    assert kb.inline_keyboard[-1][0].callback_data == "subcat:3"
    assert "رشق متابعين" in kb.inline_keyboard[0][0].text


def test_fmt_price_strips_trailing_zeros():
    assert _fmt_price(Decimal("0.0400")) == "0.04"
    assert _fmt_price(Decimal("40")) == "40"
    assert _fmt_price(Decimal("40.5")) == "40.5"


@pytest.mark.asyncio
async def test_smm_sell_price_uses_live_provider_rate_instead_of_stored_cost():
    """سعر خدمة الرشق المربوطة بمزود يُحسب من تكلفة المزود اللحظية،
    لا من cost_price_usd المخزّن (كانت الأسعار «عالقة» عند تغيّر المزود)."""
    async with async_session_maker() as session:
        category = Category(name_ar="رشق", emoji="📈", type=CategoryType.SMM)
        session.add(category)
        await session.flush()
        sub = SubCategory(category_id=category.id, name_ar="إنستغرام", emoji="📱")
        session.add(sub)
        await session.flush()
        provider = ApiProvider(
            name="TestSMM",
            type=ApiProviderType.SMM,
            api_url="https://example.test/api",
            api_key="k",
            is_active=True,
        )
        session.add(provider)
        await session.flush()
        service = ProviderService(
            api_provider_id=provider.id,
            external_service_id="ig-followers",
            name="Followers",
            rate=Decimal("0.20"),
            rate_usd=Decimal("0.20"),
            min_quantity=100,
            max_quantity=100000,
            requires_link=True,
            requires_quantity=True,
        )
        session.add(service)
        await session.flush()

        # سعر مخزّن قديم (عالق) — 0.10 تكلفة، 0.15 بيع.
        product = Product(
            sub_category_id=sub.id,
            name_ar="متابعون إنستغرام",
            price_usd=Decimal("0.15"),
            cost_price_usd=Decimal("0.10"),
            provider_service_ref_id=service.id,
            fulfillment_type=ProductFulfillmentType.API,
            status=ProductStatus.ACTIVE,
        )
        session.add(product)
        await session.flush()

        # الهامش الافتراضي عالمي 50% → السعر 0.20 * 1.5 = 0.30 (وليس 0.15).
        sell = await MarginService.product_sell_price(session, product)
        assert sell == Decimal("0.3000")


@pytest.mark.asyncio
async def test_admin_ready_code_edit_kb_fields():
    item = ReadyCodeItem(
        name_ar="تطبيق",
        description="وصف",
        price_usd=Decimal("7"),
        instructions="خطوات",
        file_url="https://x",
        is_active=True,
    )
    kb = admin_ready_code_edit_kb(item)
    labels = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("✏️ الاسم" in x for x in labels)
    assert any("🗑 حذف" in x for x in labels)
    assert any("⚠️ تعطيل" in x for x in labels)
