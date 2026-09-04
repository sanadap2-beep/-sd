"""اختبارات: ألوان الأزرار الأصلية + حدود الهامش على MarginService."""

from __future__ import annotations

from decimal import Decimal

import pytest
from aiogram.enums import ButtonStyle
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.main_menu import build_main_menu
from keyboards.style_utils import (
    STYLE_ACCOUNT,
    STYLE_STORE,
    STYLE_WARNING,
    add_styled,
)


def test_style_constants():
    assert STYLE_STORE == ButtonStyle.SUCCESS
    assert STYLE_ACCOUNT == ButtonStyle.PRIMARY
    assert STYLE_WARNING == ButtonStyle.DANGER


def test_add_styled_sets_style():
    b = InlineKeyboardBuilder()
    add_styled(b, "متجر", "store:home", STYLE_STORE)
    button = b.as_markup().inline_keyboard[0][0]
    assert button.style == "success"
    assert button.callback_data == "store:home"


def test_main_menu_uses_native_styles():
    markup = build_main_menu([], [], completed_orders_count=12)
    buttons = {b.callback_data: b for row in markup.inline_keyboard for b in row}

    assert buttons["store:home"].style == "success"
    assert buttons["menu:account"].style == "primary"
    assert buttons["menu:deposit"].style == "primary"
    assert buttons["info:stats"].style == "primary"
    assert buttons["info:terms"].style == "danger"


def test_navigation_buttons_keep_default_style():
    from keyboards.main_menu import deposit_menu_kb

    markup = deposit_menu_kb()
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data == "back_to_main":
                assert button.style is None


def test_margin_service_exposes_limits():
    from services.margin_service import MAX_MARGIN, MIN_MARGIN, MarginService

    assert MarginService.MIN_MARGIN == MIN_MARGIN == Decimal("-95")
    assert MarginService.MAX_MARGIN == MAX_MARGIN == Decimal("1000")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("50", Decimal("50")),
        ("0", None),
        ("مسح", None),
        ("abc", False),
        ("5000", False),
        ("-99", False),
    ],
)
def test_admin_percent_parser(raw, expected):
    from handlers.admin.margins import _parse_percent

    assert _parse_percent(raw) == expected
