"""Keyboards for the agent program user pages."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def agent_home_kb(is_agent: bool) -> InlineKeyboardMarkup:
    if is_agent:
        rows = [
            [InlineKeyboardButton(text="📊 تفاصيل وكالتي", callback_data="agent:status", style="primary")],
            [InlineKeyboardButton(text="⬅️ القائمة الرئيسية", callback_data="back_to_main")],
        ]
    else:
        rows = [
            [InlineKeyboardButton(text="🔑 أدخل كود الوكالة", callback_data="agent:enter_code", style="primary")],
            [InlineKeyboardButton(text="❓ كيف يعمل البرنامج؟", callback_data="agent:how", style="primary")],
            [InlineKeyboardButton(text="⬅️ القائمة الرئيسية", callback_data="back_to_main")],
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def agent_code_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ رجوع", callback_data="agent:home")]
        ]
    )


def agent_status_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ رجوع", callback_data="agent:home")],
            [InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="back_to_main")],
        ]
    )
