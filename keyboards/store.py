"""Keyboards for the universal store hub."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


SECTION_LABELS = {
    "featured": "⭐ مختارات المتجر",
    "deals": "🔥 عروض اليوم",
    "bestsellers": "🏆 الأكثر مبيعاً",
    "instant": "⚡ تسليم فوري",
    "cheap": "💸 أقل من 2$",
    "games": "🎮 ألعاب",
    "smm": "📈 سوشيال ميديا",
    "apps": "📦 تطبيقات واشتراكات",
}


def store_home_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, label in SECTION_LABELS.items():
        b.button(text=label, callback_data=f"store:section:{key}")
    b.button(text="🔍 بحث بالمتجر", callback_data="menu:search")
    b.button(text="🛒 السلة", callback_data="menu:cart")
    b.button(text="➕ اطلب منتج غير موجود", callback_data="menu:product_request")
    b.button(text="🔙 رجوع للقائمة", callback_data="back_to_main")
    b.adjust(2, 2, 2, 2, 2, 1, 1)
    return b.as_markup()


def store_products_kb(products, section: str) -> InlineKeyboardMarkup:
    rows = []
    for product in products:
        name = product.name_ar if len(product.name_ar) <= 36 else product.name_ar[:35] + "…"
        rows.append([
            InlineKeyboardButton(
                text=f"🛒 {name} · {product.price_usd}$",
                callback_data=f"prod:{product.id}",
            )
        ])
    rows.append([
        InlineKeyboardButton(text="⬅️ أقسام المتجر", callback_data="store:home"),
        InlineKeyboardButton(text="🛒 السلة", callback_data="menu:cart"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
