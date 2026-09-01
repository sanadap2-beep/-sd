"""اختبارات الترجمة (ar/en) وعملات العرض اليومية."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from database.engine import async_session_maker
from database.models import User
from services.currency_service import CurrencyService, DISPLAY_CURRENCIES
from services.settings_service import SettingsService

_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    return json.loads((_ROOT / "locales" / name).read_text(encoding="utf-8"))


def _placeholders(value: str) -> list[str]:
    return sorted(re.findall(r"\{[a-z_0-9]+\}", value))


def test_locale_files_have_identical_key_sets():
    ar = _load("ar.json")
    en = _load("en.json")
    assert set(ar) == set(en), (
        f"اختلاف المفاتيح: ar-only={set(ar) - set(en)} en-only={set(en) - set(ar)}"
    )


def test_every_key_used_in_code_exists_in_locales():
    ar = _load("ar.json")
    en = _load("en.json")
    used: set[str] = set()
    for path in list((_ROOT / "handlers").rglob("*.py")) + list(
        (_ROOT / "keyboards").glob("*.py")
    ):
        source = path.read_text(encoding="utf-8")
        for match in re.finditer(
            r'(?:I18nService\.t|\bt)\(\s*["\']([a-z][a-z_0-9]*)["\']', source, re.S
        ):
            used.add(match.group(1))
    missing = sorted(key for key in used if key not in ar)
    assert not missing, f"مفاتيح مستخدمة بالكود وغير موجودة بالترجمة: {missing}"
    assert all(key in en for key in used)


def test_placeholders_match_between_languages():
    ar = _load("ar.json")
    en = _load("en.json")
    mismatches = [
        key
        for key in ar
        if key in en and _placeholders(str(ar[key])) != _placeholders(str(en[key]))
    ]
    assert not mismatches, f"اختلاف العناصر بين ar/en: {mismatches}"


def test_supported_display_currencies():
    assert set(DISPLAY_CURRENCIES) == {"USD", "EUR", "EGP", "SYP"}


@pytest.mark.asyncio
async def test_display_rate_from_settings_and_fallback():
    async with async_session_maker() as session:
        # القيمة الافتراضية عند عدم الضبط
        rate = await CurrencyService.get_display_rate(session, "EUR")
        assert rate == Decimal("0.92")

        # الأدمن يضبط سعراً يومياً جديداً
        await SettingsService.set(session, "usd_to_eur_rate", "0.95")
        rate = await CurrencyService.get_display_rate(session, "EUR")
        assert rate == Decimal("0.95")

        # سعر غير صالح (صفر) → الرجوع للافتراضي بدل كسر العرض
        await SettingsService.set(session, "usd_to_egp_rate", "0")
        assert await CurrencyService.get_display_rate(session, "EGP") == Decimal("48.5")


@pytest.mark.asyncio
async def test_convert_and_format_display_currency():
    async with async_session_maker() as session:
        await SettingsService.set(session, "usd_to_syp_rate", "15000")

        converted = await CurrencyService.convert_from_usd(
            Decimal("5"), "SYP", session
        )
        assert converted == Decimal("75000")

        arabic = await CurrencyService.format_from_usd(
            Decimal("5"), "SYP", session, language="ar"
        )
        assert arabic == "75,000 ل.س"

        english = await CurrencyService.format_from_usd(
            Decimal("5"), "SYP", session, language="en"
        )
        assert english == "75,000 SYP"

        usd = await CurrencyService.format_from_usd(Decimal("5"), "USD", session)
        assert usd == "$5.00"

        # عملة غير معروفة → USD
        assert CurrencyService.normalize_display_currency("XXX") == "USD"
        assert CurrencyService.normalize_display_currency(None) == "USD"


@pytest.mark.asyncio
async def test_dual_format_for_user():
    async with async_session_maker() as session:
        await SettingsService.set(session, "usd_to_egp_rate", "50")
        user = User(
            telegram_id=990,
            full_name="Currency User",
            display_currency="EGP",
            language_code="en",
        )
        session.add(user)
        await session.commit()

        dual = await CurrencyService.format_dual(Decimal("2"), user, session)
        assert dual == "$2.00 (≈ 100.00 E£)"

        # مستخدم USD → دولار فقط بلا إضافة
        usd_user = User(telegram_id=991, full_name="USD User")
        session.add(usd_user)
        await session.commit()
        assert await CurrencyService.format_dual(
            Decimal("2"), usd_user, session
        ) == "$2.00"


@pytest.mark.asyncio
async def test_display_currency_column_exists_and_persists():
    async with async_session_maker() as session:
        user = User(telegram_id=992, full_name="Persist", display_currency="EUR")
        session.add(user)
        await session.commit()

        from sqlalchemy import select

        result = await session.execute(
            select(User).where(User.telegram_id == 992)
        )
        fresh = result.scalar_one()
        assert fresh.display_currency == "EUR"
