"""أزرار برنامج الولاء."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def loyalty_kb(
    points: int,
    can_claim: bool,
    min_redeem: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_claim:
        builder.button(
            text="🎁 استلام مكافأة اليوم",
            callback_data="loyalty:daily", style="primary",
        )
    for amount in (min_redeem, 500, 1000, 2500):
        if amount >= min_redeem and amount <= points:
            builder.button(
                text=f"💵 استبدال {amount} نقطة",
                callback_data=f"loyalty:redeem:{amount}", style="primary",
            )
    builder.button(
        text="🔄 تحديث",
        callback_data="menu:loyalty", style="primary",
    )
    builder.button(
        text="🔙 القائمة الرئيسية",
        callback_data="back_to_main",
    )
    builder.adjust(1)
    return builder.as_markup()
