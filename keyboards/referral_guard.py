"""أزرار التحقق البشري لحماية الإحالة."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def human_check_markup() -> InlineKeyboardMarkup:
    """أزرار الأرقام ٠-٩ — الصحيح محفوظ في حالة المحادثة لا في النداء."""
    b = InlineKeyboardBuilder()
    for digit in range(10):
        b.button(text=f"{digit}", callback_data=f"rg:ans:{digit}")
    b.adjust(5)
    return b.as_markup()
