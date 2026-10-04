"""اختبارات رسالة تأكيد الطلب الموحّدة + نص الشاشة الأولى (الهوية).

الهدف من هذه الاختبارات تثبيت شكل الرسالة التي تصل المستخدم بعد كل طلب:
تفاصيل كاملة، تسمية هدف مختلفة حسب نوع الخدمة، وخاتمة «سيتم إشعارك عند
اكتمال الخدمة» — إضافةً إلى رأس القائمة الرئيسية الجديد.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import Category, CategoryType, Product, SubCategory
from services.branding_service import BrandingService
from services.order_confirmation_service import OrderConfirmationService as OCS

PENDING_NOTICE = "سيتم إشعارك عند اكتمال الخدمة"


def _product(name: str, category_type: CategoryType, **flags):
    defaults = dict(
        name_ar=name,
        sub_category=SimpleNamespace(
            category=SimpleNamespace(type=category_type, name_ar="قسم اختبار")
        ),
        custom_input_placeholder=None,
        requires_player_id=False,
        requires_link=False,
        requires_username=False,
    )
    defaults.update(flags)
    return SimpleNamespace(**defaults)


# ══════════════════ رسالة الطلب الموحّدة ══════════════════


@pytest.mark.asyncio
async def test_unified_confirmation_carries_full_details():
    order = SimpleNamespace(id=4821)
    product = _product("شحن 325 UC — ببجي", CategoryType.GAMES, requires_player_id=True)

    text = await OCS.unified(
        order=order,
        product=product,
        target="5123456789",
        quantity=2,
        price_usd=Decimal("5.00"),
        discount_usd=Decimal("0.50"),
        discount_label="الكوبون",
        cashback_usd=Decimal("0.10"),
        status="تم إرسال الطلب للمزود",
        balance_after=Decimal("12.30"),
    )

    assert "تم إنشاء طلبك بنجاح" in text
    assert "قسم شحن الألعاب" in text
    assert "شحن 325 UC — ببجي" in text
    assert "معرّف اللاعب" in text  # تسمية الهدف تتكيّف مع الخدمة
    assert "5123456789" in text
    assert "الكمية" in text and "2" in text
    assert "#4821" in text
    assert "5.00$" in text and "-0.50$" in text and "4.50$" in text and "12.30$" in text
    assert "تم إرسال الطلب للمزود" in text
    assert "الوقت المتوقع" in text
    assert text.rstrip().endswith(f"🔔 <b>{PENDING_NOTICE}</b>")


@pytest.mark.asyncio
async def test_unified_confirmation_labels_smm_target_as_link():
    product = _product("متابعون إنستغرام", CategoryType.SMM, requires_link=True)

    text = await OCS.unified(
        order=SimpleNamespace(id=99),
        product=product,
        target="https://instagram.com/someone",
        quantity=1000,
        price_usd=Decimal("1.20"),
        status="بانتظار التنفيذ",
    )

    assert "قسم الرشق" in text
    assert "الرابط:" in text and "معرّف اللاعب" not in text
    assert PENDING_NOTICE in text


@pytest.mark.asyncio
async def test_unified_confirmation_instant_delivery_drops_pending_notice():
    product = _product("اشتراك شير", CategoryType.SUBSCRIPTIONS)

    text = await OCS.unified(
        order=SimpleNamespace(id=7),
        product=product,
        price_usd=Decimal("3.00"),
        status="مكتمل - توصيل فوري",
        delivery_html="<code>user: pass</code>",
        title="تم تسليم طلبك فوراً",
        emoji="🎉",
    )

    assert "تم تسليم طلبك فوراً" in text
    assert "user: pass" in text
    assert PENDING_NOTICE not in text


@pytest.mark.asyncio
async def test_unified_confirmation_escapes_html_from_product_and_target():
    product = _product("شحن <b>مجاني</b> & خصم", CategoryType.GAMES)

    text = await OCS.unified(
        order=SimpleNamespace(id=3),
        product=product,
        target="<script>alert(1)</script>",
        price_usd=Decimal("1.00"),
    )

    assert "<b>مجاني</b>" not in text
    assert "&lt;b&gt;مجاني&lt;/b&gt;" in text
    assert "<script>" not in text


@pytest.mark.asyncio
async def test_unified_confirmation_reads_category_from_db_when_relation_not_loaded():
    """المنتج المحمّل بـ session.get يفتح علاقة sub_category فقط عند الطلب.

    القراءة المباشرة ترفع MissingGreenlet على جلسة غير متزامنة — الرسالة
    يجب أن تجلب القسم باستعلام أعمدة بديلاً عن ذلك.
    """
    async with async_session_maker() as session:
        category = Category(name_ar="قسم الألعاب", type=CategoryType.GAMES)
        session.add(category)
        await session.flush()
        sub = SubCategory(category_id=category.id, name_ar="ببجي")
        session.add(sub)
        await session.flush()
        product = Product(sub_category_id=sub.id, name_ar="325 UC", price_usd=Decimal("5"))
        session.add(product)
        await session.commit()
        product_id = product.id

    async with async_session_maker() as session:
        loaded = await session.get(Product, product_id)  # بلا تحميل مسبق للعلاقات
        text = await OCS.unified(
            order=SimpleNamespace(id=11),
            product=loaded,
            price_usd=Decimal("5.00"),
            status="بانتظار التنفيذ",
        )

    assert "قسم شحن الألعاب" in text
    assert PENDING_NOTICE in text


# ══════════════════ الأرقام والجلسات الجاهزة ══════════════════


def test_number_confirmation_keeps_order_details_and_wording():
    text = OCS.number(
        order=SimpleNamespace(id=555),
        phone_number="+96170123456",
        service_name="واتساب",
        country_name="لبنان",
        flag="🇱🇧",
        price_usd=Decimal("0.35"),
        balance_after=Decimal("1.18"),
        timeout_minutes=5,
    )

    assert "تم شراء الرقم" in text  # يثبّت نصاً يعتمد عليه اختبار قديم
    assert "+96170123456" in text
    assert "واتساب" in text and "لبنان" in text
    assert "0.35$" in text and "1.18$" in text
    assert "5 دقيقة" in text
    assert text.rstrip().endswith(f"🔔 <b>{PENDING_NOTICE}</b>")


def test_tg_ready_confirmation_shows_phone_and_instructions():
    text = OCS.tg_ready(
        item=SimpleNamespace(id=31),
        country_name="أمريكا",
        flag="🇺🇸",
        phone_number="+12025550142",
        price_usd=Decimal("2.00"),
        stock_left=17,
    )

    assert "+12025550142" in text
    assert "أمريكا" in text and "17" in text
    assert "طلب الكود" in text
    assert PENDING_NOTICE in text


def test_bulk_numbers_confirmation_sums_batch():
    text = OCS.bulk_numbers(
        requested=10,
        succeeded=8,
        failed=2,
        net_charged_usd=Decimal("2.80"),
        refunded_usd=Decimal("0.70"),
        service_name="تيليجرام",
        country_name="الهند",
        flag="🇮🇳",
    )

    assert "10" in text and "8" in text and "2" in text
    assert "2.80$" in text and "0.70$" in text
    assert PENDING_NOTICE in text


# ══════════════════ الشاشة الأولى (الهوية) ══════════════════


@pytest.mark.asyncio
async def test_main_menu_header_shows_branding_features_and_balance():
    header = await BrandingService.main_menu_header("$1.18", "ar")

    assert "LUX STORE" in header
    assert "شحن سريع وآمن" in header
    assert "طرق دفع متعددة" in header
    assert "بأسرع وقت" in header
    assert "خدمة موثوقة" in header
    assert "$1.18" in header
    assert header.rstrip().endswith("اختر الخدمة التي تريدها من القائمة بالأسفل 👇")


@pytest.mark.asyncio
async def test_main_menu_header_uses_admin_store_name_from_settings():
    from services.settings_service import SettingsService

    async with async_session_maker() as session:
        await SettingsService.set(session, "store_name", "متجر النور")
        await SettingsService.set(session, "main_menu_features", "🔥 عرض الصيف\n⚡ تنفيذ فوري")

    header = await BrandingService.main_menu_header("$9.99", "ar")

    assert "متجر النور" in header
    assert "عرض الصيف" in header and "تنفيذ فوري" in header
    assert "طرق دفع متعددة" not in header  # حلت ميزات الأدمن محل الافتراضية


@pytest.mark.asyncio
async def test_start_main_header_wires_branding_and_falls_back_safely(monkeypatch):
    """_main_header يبني رأس الهوية، ويعود للنص القديم عند أي فشل."""
    from services import branding_service
    from handlers import start as start_handler

    async def fake_balance(*args, **kwargs):
        return "$1.18"

    monkeypatch.setattr(start_handler.CurrencyService, "format_dual", staticmethod(fake_balance))

    user = SimpleNamespace(balance=Decimal("1.18"), language_code="ar")
    header = await start_handler._main_header(None, user)
    assert "LUX STORE" in header and "$1.18" in header

    async def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(branding_service.BrandingService, "main_menu_header", staticmethod(boom))
    fallback = await start_handler._main_header(None, user)
    assert "القائمة الرئيسية" in fallback and "$1.18" in fallback
