"""أزرار تقييم المنتجات."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def rating_kb(order_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for rating in range(1, 6):
        builder.button(
            text="⭐" * rating,
            callback_data=f"review:rating:{order_id}:{rating}",
        )
    builder.button(text="❌ إلغاء", callback_data="menu:account", style="primary")
    builder.adjust(1)
    return builder.as_markup()


def review_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ تخطي التعليق", callback_data="review:skip_comment")
    builder.button(text="🔙 الحساب", callback_data="menu:account")
    builder.adjust(1)
    return builder.as_markup()
