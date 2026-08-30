"""
أزرار مشتركة تُستخدم في أكثر من مكان.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def check_subscription_kb(
    channels,
    check_cb: str = "check_subscription",
) -> InlineKeyboardMarkup:
    """أزرار الاشتراك الإجباري."""
    builder = InlineKeyboardBuilder()
    for ch in channels:
        link = ch.username_or_link or f"https://t.me/c/{str(ch.chat_id).replace('-100', '')}"
        builder.row(
            InlineKeyboardButton(
                text=f"📢 {ch.title or 'قناة'}",
                url=link,
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="✅ تحقق من الاشتراك",
            callback_data=check_cb,
        )
    )
    return builder.as_markup()


def yes_no_kb(
    yes_data: str,
    no_data: str,
) -> InlineKeyboardMarkup:
    """زر نعم / لا عام."""
    b = InlineKeyboardBuilder()
    b.button(text="✅ نعم", callback_data=yes_data)
    b.button(text="❌ لا", callback_data=no_data)
    b.adjust(2)
    return b.as_markup()


def cancel_kb(
    cancel_data: str = "back_to_main",
) -> InlineKeyboardMarkup:
    """زر إلغاء فقط."""
    b = InlineKeyboardBuilder()
    b.button(text="❌ إلغاء", callback_data=cancel_data)
    return b.as_markup()


def pagination_kb(
    base_callback: str,
    current_page: int,
    total_pages: int,
    back_callback: str = "back_to_main",
) -> InlineKeyboardMarkup:
    """أزرار تنقل بين الصفحات."""
    b = InlineKeyboardBuilder()
    if current_page > 0:
        b.button(
            text="◀️ السابق",
            callback_data=f"{base_callback}:{current_page - 1}",
        )
    b.button(
        text=f"📄 {current_page + 1}/{total_pages}",
        callback_data="noop",
    )
    if current_page < total_pages - 1:
        b.button(
            text="التالي ▶️",
            callback_data=f"{base_callback}:{current_page + 1}",
        )
    b.button(text="🔙 رجوع", callback_data=back_callback)
    if total_pages > 1:
        b.adjust(3, 1)
    else:
        b.adjust(1, 1)
    return b.as_markup()
