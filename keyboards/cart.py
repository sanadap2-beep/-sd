"""Shopping cart keyboard."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def cart_kb(items) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        builder.button(
            text=f"🗑 إزالة {item.product.name_ar[:25]}",
            callback_data=f"cart:remove:{item.product_id}", style="danger",
        )
    if items:
        builder.button(text="🧾 تنفيذ السلة", callback_data="cart:checkout", style="primary")
        builder.button(text="🧹 تفريغ السلة", callback_data="cart:clear", style="primary")
    builder.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()


def cart_add_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 عرض السلة", callback_data="menu:cart", style="primary")
    builder.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()
