"""أزرار العروض الذكية."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _discount_label(promotion) -> str:
    if promotion.discount_type.value == "percent":
        return f"-{promotion.discount_value:g}%"
    return f"-{promotion.discount_value:g}$"


def promotions_kb(promotions) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for promotion in promotions:
        name = promotion.product.name_ar if promotion.product else promotion.name
        builder.button(
            text=f"🔥 {name[:26]} {_discount_label(promotion)}",
            callback_data=f"prod:{promotion.product_id}", style="success",
        )
    builder.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()


def admin_promotions_kb(promotions) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for promotion in promotions:
        state = "🟢" if promotion.is_active else "⚪"
        builder.button(
            text=f"{state} #{promotion.id} {promotion.name[:30]}",
            callback_data=f"admin:promo_view:{promotion.id}", style="primary",
        )
    builder.button(text="➕ عرض جديد", callback_data="admin:promo_add", style="primary")
    builder.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    builder.adjust(1)
    return builder.as_markup()


def admin_promotion_types_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 نسبة مئوية", callback_data="admin:promo_type:percent", style="primary")
    builder.button(text="💵 مبلغ ثابت", callback_data="admin:promo_type:fixed", style="primary")
    builder.button(text="❌ إلغاء", callback_data="admin:promotions", style="primary")
    builder.adjust(1)
    return builder.as_markup()


def admin_promotion_detail_kb(promotion) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    state = "⚪ تعطيل" if promotion.is_active else "🟢 تفعيل"
    builder.button(
        text=state,
        callback_data=f"admin:promo_toggle:{promotion.id}", style="primary",
    )
    builder.button(
        text="🗑 حذف",
        callback_data=f"admin:promo_delete:{promotion.id}", style="danger",
    )
    builder.button(text="🔙 العروض", callback_data="admin:promotions")
    builder.adjust(2, 1)
    return builder.as_markup()
