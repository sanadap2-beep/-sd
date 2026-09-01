"""أزرار حالة الخدمات."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def status_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 تحديث", callback_data="menu:status")
    builder.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()
