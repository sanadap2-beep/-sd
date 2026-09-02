"""
أزرار شحن الألعاب والتطبيقات.
تُبنى ديناميكياً من قاعدة البيانات.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import SubCategory, Product
from services.smm_catalog import button_label


def _short_name(name: str, limit: int = 42) -> str:
    """تقصير اسم المنتج حتى لا يكسر حد طول زر تيليجرام (64 حرفاً)."""
    name = (name or "").strip()
    if len(name) <= limit:
        return name
    return name[: limit - 1].rstrip() + "…"


def sub_categories_kb(
    category_id: int,
    sub_categories: list[SubCategory],
) -> InlineKeyboardMarkup:
    """قائمة الأقسام الفرعية لقسم رئيسي."""
    b = InlineKeyboardBuilder()
    for sub in sub_categories:
        b.button(
            text=button_label(sub.name_ar, sub.emoji),
            callback_data=f"subcat:{sub.id}",
        )
    b.button(
        text="🔙 رجوع للقائمة",
        callback_data="back_to_main",
    )
    b.adjust(2)
    return b.as_markup()


def sections_kb(
    category_id: int,
    sections: list[tuple[SubCategory, int]],
) -> InlineKeyboardMarkup:
    """قائمة الأقسام الداخلية لتطبيق (متابعون/لايكات/مشاهدات...).

    كل عنصر: ``(القسم الداخلي، عدد منتجاته المفعلة)``.
    """
    b = InlineKeyboardBuilder()
    for section, count in sections:
        label = button_label(section.name_ar, section.emoji)
        b.button(
            text=f"{label} ({count})",
            callback_data=f"subcat:{section.id}",
        )
    b.button(
        text="🔙 رجوع",
        callback_data=f"cat:{category_id}",
    )
    b.adjust(1)
    return b.as_markup()


def products_kb(
    sub_category_id: int,
    products: list[Product],
    category_id: int,
    back_sub_id: int | None = None,
) -> InlineKeyboardMarkup:
    """قائمة المنتجات مع الأسعار بالدولار.

    ``back_sub_id`` يُستخدم للمنتجات داخل قسم داخلي (تطبيق): الزر «رجوع»
    يعيد إلى التطبيق بدل قائمة التطبيقات.
    """
    b = InlineKeyboardBuilder()
    for p in products:
        b.button(
            text=f"{_short_name(p.name_ar)} - {p.price_usd}$",
            callback_data=f"prod:{p.id}",
        )
    if back_sub_id is not None:
        back_callback = f"subcat:{back_sub_id}"
    else:
        back_callback = f"cat:{category_id}"
    b.button(
        text="🔙 رجوع",
        callback_data=back_callback,
    )
    b.adjust(1)
    return b.as_markup()


def product_confirm_kb(
    product_id: int,
    sub_category_id: int,
) -> InlineKeyboardMarkup:
    """تأكيد شراء منتج مع اختصار للمفضلة."""
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ تأكيد الشراء",
        callback_data=f"prod_confirm:{product_id}",
    )
    b.button(
        text="🎟 لدي كوبون خصم",
        callback_data=f"prod_coupon:{product_id}",
    )
    b.button(
        text="⭐ إضافة/إزالة من المفضلة",
        callback_data=f"favorite:toggle:{product_id}",
    )
    b.button(
        text="🛒 إضافة إلى السلة",
        callback_data=f"cart:add:{product_id}",
    )
    b.button(
        text="🔔 تنبيه السعر/المخزون",
        callback_data=f"watch:toggle:{product_id}",
    )
    b.button(
        text="🔙 رجوع",
        callback_data=f"subcat:{sub_category_id}",
    )
    b.adjust(1)
    return b.as_markup()


def product_search_results_kb(products) -> InlineKeyboardMarkup:
    """نتائج بحث المنتجات للمستخدم."""
    b = InlineKeyboardBuilder()
    for product in products:
        b.button(
            text=f"{_short_name(product.name_ar)} - {product.price_usd}$",
            callback_data=f"prod:{product.id}",
        )
    b.button(text="🔎 بحث جديد", callback_data="menu:search")
    b.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def favorites_kb(products) -> InlineKeyboardMarkup:
    """قائمة المنتجات المفضلة مع حذف سريع."""
    b = InlineKeyboardBuilder()
    for product in products:
        b.button(
            text=f"📦 {_short_name(product.name_ar)} - {product.price_usd}$",
            callback_data=f"prod:{product.id}",
        )
        b.button(
            text="🗑 إزالة",
            callback_data=f"favorite:remove:{product.id}",
        )
    b.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    b.adjust(2, 1)
    return b.as_markup()


def product_confirm_with_coupon_kb(
    product_id: int,
    sub_category_id: int,
    coupon_code: str,
    discount_usd: str,
) -> InlineKeyboardMarkup:
    """تأكيد شراء منتج بعد تطبيق كوبون."""
    b = InlineKeyboardBuilder()
    b.button(
        text=f"✅ تأكيد الشراء (خصم {discount_usd}$)",
        callback_data=(f"prod_confirm_coupon:{product_id}:{coupon_code}"),
    )
    b.button(
        text="🔙 رجوع",
        callback_data=f"subcat:{sub_category_id}",
    )
    b.adjust(1)
    return b.as_markup()
