from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def language_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🇸🇾 العربية", callback_data="language:set:ar")
    builder.button(text="🇬🇧 English", callback_data="language:set:en")
    builder.button(
        text="🔙 القائمة الرئيسية / Main menu", callback_data="back_to_main"
    )
    builder.adjust(2, 1)
    return builder.as_markup()
