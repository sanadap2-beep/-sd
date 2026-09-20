"""Tests for the numbers board UI (25/page grid) and country arabization."""

from __future__ import annotations

from decimal import Decimal

from database.engine import async_session_maker
from database.models import Country
from keyboards.numbers import COUNTRIES_PER_PAGE, countries_price_kb
from services.country_localization_service import (
    display_flag,
    display_name,
    heal_countries,
    localize_name,
)
from services.number_catalog_service import BoardEntry


def _entries(count: int) -> list[BoardEntry]:
    return [
        BoardEntry(
            code=f"c{i}",
            name_ar=f"دولة {i}",
            flag="🌍",
            cost_usd=Decimal(str(0.1 * (i + 1))),
            sell_usd=Decimal(str(0.2 * (i + 1))),
            cid=101 + i,
        )
        for i in range(count)
    ]


# ══════════════ 25 دولة/صفحة بمربعات ══════════════


def test_countries_per_page_is_25():
    assert COUNTRIES_PER_PAGE == 25


def test_price_kb_layout_two_per_row():
    # 26 دولة → الصفحة الأولى فيها 25 و25 = 12 صف بمربعين + صف بزر
    # المرجع بالزر هو الرقم الداخلي (cid) لا الكود — callback_data ≤ 64B
    kb = countries_price_kb("wa", _entries(26), page=0)
    rows = kb.inline_keyboard
    assert rows[0][0].callback_data == "num_country:wa:101"
    assert rows[0][1].callback_data == "num_country:wa:102"
    assert rows[1][0].callback_data == "num_country:wa:103"
    assert rows[11][0].callback_data == "num_country:wa:122"
    assert rows[11][1].callback_data == "num_country:wa:123"
    # الصف 12: 25 فردي → زر واحد فقط
    assert len(rows[12]) == 1
    assert rows[12][0].callback_data == "num_country:wa:125"
    # صف التنقل (صفحة 1 من 2): [الصفحة، التالي] ثم صف الرجوع
    nav = rows[13]
    assert any("التالي" in (btn.text or "") for btn in nav)
    assert not any("السابق" in (btn.text or "") for btn in nav)


def test_price_kb_second_page_and_paging():
    kb = countries_price_kb("wa", _entries(60), page=1)
    rows = kb.inline_keyboard
    # الصفحة الثانية: c25..c49 → مربعان + مربعان... + زر
    assert rows[0][0].callback_data == "num_country:wa:126"
    assert rows[0][1].callback_data == "num_country:wa:127"
    assert rows[12][0].callback_data == "num_country:wa:150"
    # صف التنقل يحتوي السابق والتالي
    nav = rows[13]
    assert any("السابق" in (btn.text or "") for btn in nav)
    assert any("التالي" in (btn.text or "") for btn in nav)
    # 60 دولة = 3 صفحات
    assert any(btn.text == "📄 2/3" for btn in nav)


def test_price_kb_shows_flag_and_price():
    kb = countries_price_kb("wa", _entries(2), page=0)
    first = kb.inline_keyboard[0][0]
    # العلم + الاسم + السعر بالدولار
    assert first.text.startswith("🌍 دولة 0 — ")
    assert first.text.endswith("$")


def test_price_kb_last_page_no_next():
    kb = countries_price_kb("wa", _entries(30), page=1)
    # 30 دولة = صفحتان؛ الصفحة الثانية: 5 أزرار → صفان بمربعين + صف بزر
    rows = kb.inline_keyboard
    assert rows[0][0].callback_data == "num_country:wa:126"
    assert rows[2][0].callback_data == "num_country:wa:130"
    nav = rows[3]
    assert any("السابق" in (btn.text or "") for btn in nav)
    assert not any("التالي" in (btn.text or "") for btn in nav)


# ══════════════ تعريب أسماء الدول ══════════════


def test_localize_name_english_to_arabic():
    name, flag = localize_name("Turkey")
    assert name == "تركيا"
    assert flag == "🇹🇷"

    name, flag = localize_name("SaudiArabia")
    assert name == "السعودية"
    assert flag == "🇸🇦"


def test_localize_name_arabic_untouched():
    name, flag = localize_name("سوريا")
    assert name == "سوريا"
    assert flag == "🌍"


def test_display_name_and_flag_from_db_row():
    eng = Country(code="eng", name_ar="France", flag="🌍")
    assert display_name(eng) == "فرنسا"
    assert display_flag(eng) == "🇫🇷"

    ar = Country(code="ar", name_ar="العراق", flag="🇮🇶")
    assert display_name(ar) == "العراق"
    assert display_flag(ar) == "🇮🇶"


async def test_heal_countries_fixes_db_and_keeps_provider_codes():
    async with async_session_maker() as session:
        c = Country(
            code="fra_86",
            name_ar="France",
            flag="🌍",
            herosms_code="86",
            fivesim_code="fr",
        )
        ar = Country(code="iraq", name_ar="العراق", flag="🇮🇶", herosms_code="101")
        session.add_all([c, ar])
        await session.commit()

        fixed = await heal_countries(session)

        assert fixed == 1
        c = await session.get(Country, c.id)
        ar = await session.get(Country, ar.id)
        # الاسم صار عربياً والعلم مضبوطاً
        assert c.name_ar == "فرنسا"
        assert c.flag == "🇫🇷"
        # أكواد المزودين لم تتأثر إطلاقاً
        assert c.herosms_code == "86"
        assert c.fivesim_code == "fr"
        # الدولة العربية الصحيحة لم تُمس
        assert ar.name_ar == "العراق"
