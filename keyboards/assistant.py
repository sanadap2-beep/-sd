"""لوحة المساعد الذكي."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def assistant_start_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 تجربة أخرى", callback_data="menu:assistant")
    builder.button(text="📣 اطلب خدمة", callback_data="menu:product_request", style="success")
    builder.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()
