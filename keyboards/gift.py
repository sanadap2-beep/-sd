"""أزرار بطاقات الهدايا."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def gift_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 استبدال بطاقة", callback_data="gift:redeem", style="primary")
    builder.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()


def gift_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ إلغاء", callback_data="menu:gift", style="primary")
    return builder.as_markup()


def admin_gift_codes_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ إنشاء بطاقة", callback_data="admin:gift_create", style="primary")
    builder.button(text="📋 آخر البطاقات", callback_data="admin:gift_recent", style="primary")
    builder.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    builder.adjust(1)
    return builder.as_markup()


def admin_gift_recent_kb(gifts) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for gift in gifts:
        state = "🟢" if gift.is_active else "⚪"
        builder.button(
            text=f"{state} {gift.code} · {gift.used_count}/{gift.max_uses}",
            callback_data=f"admin:gift_revoke:{gift.id}", style="danger",
        )
    builder.button(text="🔙 بطاقات الهدايا", callback_data="admin:gift_codes")
    builder.adjust(1)
    return builder.as_markup()


def admin_gift_max_uses_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="استخدام واحد", callback_data="admin:gift_uses:1", style="primary")
    builder.button(text="10 استخدامات", callback_data="admin:gift_uses:10", style="primary")
    builder.button(text="100 استخدام", callback_data="admin:gift_uses:100", style="primary")
    builder.adjust(1)
    return builder.as_markup()
