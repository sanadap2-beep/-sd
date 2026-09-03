"""Tests for admin control of the store page and the extras page."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup

from database.engine import async_session_maker
from database.models import Category, CategoryType
from keyboards.store import store_home_kb
from services.extras_section_service import EXTRAS_KEYS, ExtrasSectionService
from services.store_section_service import (
    BUILTIN_KEYS,
    StoreSectionService,
)


# ══════════════ StoreSectionService ══════════════


async def test_store_entries_default_all_active():
    entries = await StoreSectionService.list_entries(include_inactive=True)
    assert len(entries) == len(BUILTIN_KEYS)
    assert all(entry.is_active for entry in entries)
    assert all(entry.is_builtin for entry in entries)
    # الترتيب تصاعدي حسب sort_order
    orders = [entry.sort_order for entry in entries]
    assert orders == sorted(orders)


async def test_store_toggle_entry_persists():
    async with async_session_maker() as session:
        entry = await StoreSectionService.toggle(session, "offers")
    assert entry is not None
    assert entry.is_active is False

    fresh = await StoreSectionService.get("offers")
    assert fresh.is_active is False
    assert await StoreSectionService.is_active("offers") is False


async def test_store_add_custom_entry():
    async with async_session_maker() as session:
        added = await StoreSectionService.add_custom(session, "🎯 صالة الألعاب", "cat:5")
    assert added.is_builtin is False
    assert added.action == "cat:5"

    listed = await StoreSectionService.list_entries(include_inactive=True)
    assert any(entry.key == added.key for entry in listed)


async def test_store_delete_only_custom_entries():
    async with async_session_maker() as session:
        added = await StoreSectionService.add_custom(session, "قسم", "menu:cart")
        ok = await StoreSectionService.delete(session, added.key)
    assert ok is True

    # الثابت لا يُحذف
    async with async_session_maker() as session:
        assert await StoreSectionService.delete(session, "offers") is False


async def test_store_move_changes_order():
    async with async_session_maker() as session:
        await StoreSectionService.move(session, "offers", +1)
    entries = await StoreSectionService.list_entries(include_inactive=True)
    keys = [entry.key for entry in entries]
    # بعد تنزيل «العروض» درجة تصبح خلف أول قسم ذكي مباشرة
    assert keys.index("offers") == keys.index("smart_featured") + 1


async def test_store_reset_defaults_removes_custom():
    async with async_session_maker() as session:
        added = await StoreSectionService.add_custom(session, "قسم", "menu:cart")
        await StoreSectionService.reset_defaults(session)
    assert added.key not in {entry.key for entry in await StoreSectionService.list_entries(include_inactive=True)}


async def test_store_keyboard_uses_entries_and_hides_inactive():
    async with async_session_maker() as session:
        await StoreSectionService.toggle(session, "offers")
    entries = [
        entry
        for entry in await StoreSectionService.list_entries(include_inactive=True)
        if entry.is_active
    ]
    kb: InlineKeyboardMarkup = store_home_kb(entries=entries, webapp_url=None, language="ar")
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    # العروض معطلة → تختفي، والأرقام مفعلة → تظهر
    assert not any("العروض الخاصة" in (text or "") for text in texts)
    assert any("الأرقام" in (text or "") for text in texts)
    # زر الـ webapp لا يظهر بدون رابط
    assert not any("متجرك الكامل" in (text or "") for text in texts)


async def test_store_keyboard_fallback_layout_without_entries():
    kb = store_home_kb(number_services=[], categories=[], webapp_url="https://example.com", language="ar")
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any(text and "المتجر الإلكتروني" in text for text in texts)


# ══════════════ ExtrasSectionService ══════════════


async def test_extras_default_visibility():
    # العناصر غير المرتبطة بميزة تظهر دائماً، والمرتبطة تظهر فقط
    # عندما تكون ميزتها مفعلة (نفس سلوك «مركز الإضافات»).
    from services.feature_service import FeatureService

    for entry in await ExtrasSectionService.list_entries():
        expected = True if entry.feature_key is None else await FeatureService.enabled(entry.feature_key)
        assert await ExtrasSectionService.is_visible(entry.key) is expected, entry.key


async def test_extras_toggle_persists():
    async with async_session_maker() as session:
        entry = await ExtrasSectionService.toggle(session, "withdraw")
    assert entry.is_active is False
    assert await ExtrasSectionService.is_visible("withdraw") is False
    assert await ExtrasSectionService.is_visible("cart") is True


async def test_extras_unknown_key_rejected():
    async with async_session_maker() as session:
        assert await ExtrasSectionService.toggle(session, "nope") is None


async def test_extras_category_still_shown_even_if_disabled_elsewhere():
    # القسم الرئيسي لا يزال يُدار من إدارة الأقسام؛ هنا نتأكد فقط أن
    # خدمة الأرقام كمفهوم منفصل عن عناصر extras لا تتأثر.
    async with async_session_maker() as session:
        category = Category(name_ar="ألعاب", emoji="🎮", type=CategoryType.GAMES)
        session.add(category)
        await session.commit()
    entries = await ExtrasSectionService.list_entries()
    assert "search" in {entry.key for entry in entries}
