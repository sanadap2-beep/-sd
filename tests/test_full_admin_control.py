"""Tests for full admin control: descriptions everywhere + inner sections for any section."""

from __future__ import annotations

from decimal import Decimal

import pytest

from database.engine import async_session_maker
from database.models import (
    Category,
    CategoryType,
    Product,
    ProductDisplayType,
    ProductFulfillmentType,
    ProductStatus,
    SubCategory,
)
from keyboards.admin import admin_product_detail_kb
from keyboards.admin_categories_v2 import (
    category_detail_kb,
    sub_category_detail_kb,
)


def test_category_detail_has_desc_button():
    cat = Category(name_ar="الأكواد", emoji="🎟", type=CategoryType.CODES)
    kb = category_detail_kb(cat)
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "admin:cat_edit:desc:1" in data or any(
        d.startswith("admin:cat_edit:desc:") for d in data
    ), data


def test_sub_detail_allows_inner_sections_for_non_smm():
    """زر «إضافة قسم داخلي» متاح لأي قسم فرعي جذر — ليس الرشق فقط."""
    sub = SubCategory(
        category_id=1,
        name_ar="الأكواد الرقمية",
        emoji="🎟",
        parent_sub_category_id=None,
    )
    kb = sub_category_detail_kb(sub)
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(d.startswith("admin:subcat_add_child:") for d in data), data


def test_sub_detail_inner_only_for_root():
    """القسم الداخلي (فرع) لا يعرض زر إضافة قسم داخلي داخله (تسلسل واحد)."""
    sub = SubCategory(
        category_id=1,
        name_ar="متابعون",
        emoji="👤",
        parent_sub_category_id=5,
    )
    kb = sub_category_detail_kb(sub)
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert not any(d.startswith("admin:subcat_add_child:") for d in data), data


def test_product_detail_has_desc_button():
    p = Product(
        name_ar="منتج",
        price_usd=Decimal("1"),
        status=ProductStatus.ACTIVE,
    )
    kb = admin_product_detail_kb(p, sub_category_id=1)
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert any(d.startswith("admin:prod_edit_desc:") for d in data), data


async def test_category_has_description_column():
    from sqlalchemy import text

    async with async_session_maker() as session:
        await session.execute(
            text("PRAGMA table_info(categories)")
        )
        result = await session.execute(text("PRAGMA table_info(categories)"))
        cols = [row[1] for row in result.all()]
        assert "description" in cols, cols


async def test_category_description_roundtrip():
    async with async_session_maker() as session:
        cat = Category(
            name_ar="الأكواد",
            emoji="🎟",
            type=CategoryType.CODES,
            description="أكواد رقمية أصلية بضمان",
        )
        session.add(cat)
        await session.commit()
        await session.refresh(cat)
        assert cat.description == "أكواد رقمية أصلية بضمان"
        cat.description = None
        await session.commit()
        await session.refresh(cat)
        assert cat.description is None


async def test_show_subcategory_includes_description():
    """شرح القسم الفرعي يظهر للزبون في شاشة المنتجات/الأقسام الداخلية."""
    from handlers.games import _show_subcategory

    async with async_session_maker() as session:
        cat = Category(name_ar="الرشق", emoji="📈", type=CategoryType.SMM)
        session.add(cat)
        await session.flush()
        sub = SubCategory(
            category_id=cat.id,
            name_ar="تيك توك",
            emoji="🎵",
            description="خدمات تيك توك موثوقة وسريعة",
        )
        session.add(sub)
        await session.flush()
        product = Product(
            sub_category_id=sub.id,
            name_ar="متابعون",
            price_usd=Decimal("5"),
            cost_price_usd=Decimal("3"),
            display_type=ProductDisplayType.PER_1000,
            fulfillment_type=ProductFulfillmentType.API,
            status=ProductStatus.ACTIVE,
        )
        session.add(product)
        await session.commit()

    class FakeTarget:
        def __init__(self):
            self.text = None
            self.markup = None

        async def edit_text(self, text, reply_markup=None):
            self.text = text
            self.markup = reply_markup

        async def answer(self, text, reply_markup=None):
            self.text = text
            self.markup = reply_markup

    target = FakeTarget()
    async with async_session_maker() as session:
        sub = await session.get(SubCategory, sub.id)
        await _show_subcategory(target, session, sub, "ar")

    assert "خدمات تيك توك موثوقة وسريعة" in target.text


# ══════════════ آخر التحديثات والإضافات ══════════════


def test_admin_main_has_changelog_button():
    from keyboards.admin import admin_main_kb

    kb = admin_main_kb()
    buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "admin:changelog" in buttons


def test_admin_main_is_grouped_into_four_tabs():
    from keyboards.admin import admin_main_kb

    kb = admin_main_kb()
    buttons = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "admin:tab:finance" in buttons
    assert "admin:tab:store" in buttons
    assert "admin:tab:users" in buttons
    assert "admin:tab:system" in buttons


@pytest.mark.asyncio
async def test_changelog_screen_shows_today_updates():
    from handlers.admin.panel import CHANGELOG_ENTRIES, _changelog_chunks, admin_changelog

    chunks = _changelog_chunks(CHANGELOG_ENTRIES)
    assert chunks, "لا توجد تحديثات مسجلة"
    full = "\n".join(chunks)
    # كل ميزات اليوم موجودة
    for expected in (
        "زر المتجر الرئيسي",
        "شبكة الأرقام",
        "لوحة التوفر",
        "برنامج الوكلاء",
        "تحكم كامل بهوامش الربح",
        "ترجمة الخدمات المسحوبة تلقائياً",
        "سلطة كاملة على الأقسام والمنتجات",
        "Hyper Store",
        "المنتجات اليدوية",
        "الاشتراكات الرقمية برصد ميزان المزود",
    ):
        assert expected in full, f"نافذ من التحديثات: {expected}"
    # لا رسالة تتجاوز حد تيليجرام
    for chunk in chunks:
        assert len(chunk) <= 4096

    class FakeMessage:
        def __init__(self):
            self.edited = []

        async def edit_text(self, text, **kwargs):
            self.edited.append({"text": text, "markup": kwargs.get("reply_markup")})

        async def answer(self, text, **kwargs):
            self.edited.append({"text": text, "markup": kwargs.get("reply_markup")})

    class FakeCallback:
        def __init__(self):
            self.message = FakeMessage()
            self.answered = False

        async def answer(self, *args, **kwargs):
            self.answered = True

    cb = FakeCallback()
    await admin_changelog(cb)
    assert cb.answered
    assert cb.message.edited
    assert "آخر التحديثات والإضافات" in cb.message.edited[0]["text"]
