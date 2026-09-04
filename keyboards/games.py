"""
أزرار شحن الألعاب والتطبيقات.
تُبنى ديناميكياً من قاعدة البيانات.
"""

from decimal import Decimal

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
            callback_data=f"subcat:{sub.id}", style="success",
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
            callback_data=f"subcat:{section.id}", style="success",
        )
    b.button(
        text="🔙 رجوع",
        callback_data=f"cat:{category_id}",
    )
    b.adjust(1)
    return b.as_markup()


def _server_product_price(product, server) -> Decimal:
    """سعر الوحدة بعد هامش السيرفر (حساب ثابت/نسبة فقط بلا كمية)."""
    if server is None or server.margin_percent is None:
        return Decimal(str(product.price_usd or 0))
    cost = getattr(product, "cost_price_usd", None)
    if cost is None or Decimal(str(cost or 0)) <= 0:
        return Decimal(str(product.price_usd or 0))
    return (
        Decimal(str(cost)) * (Decimal("100") + Decimal(str(server.margin_percent))) / Decimal("100")
    ).quantize(Decimal("0.0001"))


def products_kb(
    sub_category_id: int,
    products: list[Product],
    category_id: int,
    back_sub_id: int | None = None,
    server=None,
) -> InlineKeyboardMarkup:
    """قائمة المنتجات مع الأسعار بالدولار.

    ``back_sub_id`` يُستخدم للمنتجات داخل قسم داخلي (تطبيق): الزر «رجوع»
    يعيد إلى التطبيق بدل قائمة التطبيقات.
    ``server`` يضيف زر «تغيير السيرفر» في أعلى القائمة.
    """
    b = InlineKeyboardBuilder()
    if server is not None:
        b.button(
            text=f"{server.emoji} 🔄 تغيير السيرفر: {_short_name(server.name_ar, 26)}",
            callback_data=f"subcat:{sub_category_id}", style="success",
        )
    for p in products:
        price = _server_product_price(p, server)
        b.button(
            text=f"{_short_name(p.name_ar)} - {price}$",
            callback_data=f"prod:{p.id}", style="success",
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


def store_servers_kb(
    sub_category_id: int,
    servers,
    category_id: int,
) -> InlineKeyboardMarkup:
    """اختيار سيرفر قبل عرض منتجات القسم الفرعي.

    كل سيرفر يعرض اسمه + نسبة الربح (إن ضُبطت) كي يعرف المستخدم
    الفروقات بين السيرفرات.
    """
    b = InlineKeyboardBuilder()
    for s in servers:
        margin_label = ""
        if s.margin_percent is not None:
            margin_label = f"  ({s.margin_percent}%)"
        b.button(
            text=f"{s.emoji} {_short_name(s.name_ar, 34)}{margin_label}",
            callback_data=f"svc_pick:{sub_category_id}:{s.id}", style="success",
        )
    b.button(
        text="🔙 رجوع",
        callback_data=f"cat:{category_id}",
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
        callback_data=f"prod_confirm:{product_id}", style="primary",
    )
    b.button(
        text="🎟 لدي كوبون خصم",
        callback_data=f"prod_coupon:{product_id}", style="primary",
    )
    b.button(
        text="⭐ إضافة/إزالة من المفضلة",
        callback_data=f"favorite:toggle:{product_id}",
    )
    b.button(
        text="🛒 إضافة إلى السلة",
        callback_data=f"cart:add:{product_id}", style="primary",
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
            callback_data=f"prod:{product.id}", style="success",
        )
    b.button(text="🔎 بحث جديد", callback_data="menu:search", style="success")
    b.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def favorites_kb(products) -> InlineKeyboardMarkup:
    """قائمة المنتجات المفضلة مع حذف سريع."""
    b = InlineKeyboardBuilder()
    for product in products:
        b.button(
            text=f"📦 {_short_name(product.name_ar)} - {product.price_usd}$",
            callback_data=f"prod:{product.id}", style="success",
        )
        b.button(
            text="🗑 إزالة",
            callback_data=f"favorite:remove:{product.id}", style="danger",
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
        callback_data=(f"prod_confirm_coupon:{product_id}:{coupon_code}"), style="primary",
    )
    b.button(
        text="🔙 رجوع",
        callback_data=f"subcat:{sub_category_id}",
    )
    b.adjust(1)
    return b.as_markup()
