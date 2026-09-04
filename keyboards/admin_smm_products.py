"""أزرار شاشة «🚀 منتجات قسم الرشق» في لوحة الأدمن.

مسار التنقّل: التطبيقات ← الأقسام الفرعية ← منتجات القسم.
كل الأزرار تحمل البادئة ``smmp:`` حتى لا تتعارض مع أزرار الأقسام العامة.
"""

from __future__ import annotations

from decimal import Decimal

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _short(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _price(value) -> str:
    try:
        number = Decimal(str(value or 0)).normalize()
    except Exception:
        return str(value)
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


# ══════════════ اختيار قسم رشق (عند وجود أكثر من واحد) ══════════════


def smm_categories_kb(categories) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for category in categories:
        b.button(
            text=f"{category.emoji} {category.name_ar}",
            callback_data=f"smmp:cat:{category.id}:1", style="success",
        )
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


# ══════════════ التطبيقات ══════════════


def smm_apps_kb(
    category_id: int,
    rows,
    *,
    only_with_products: bool,
    has_hidden: bool,
) -> InlineKeyboardMarkup:
    """قائمة التطبيقات مع عدد منتجات كل تطبيق."""
    b = InlineKeyboardBuilder()
    for stats in rows:
        b.button(
            text=f"{_short(stats.label, 26)} · {stats.total} منتج ({stats.active} ✅)",
            callback_data=f"smmp:app:{stats.sub.id}", style="success",
        )
    if only_with_products and has_hidden:
        b.button(
            text="👁 إظهار التطبيقات الفارغة أيضاً",
            callback_data=f"smmp:cat:{category_id}:0", style="success",
        )
    elif not only_with_products:
        b.button(
            text="🙈 إخفاء التطبيقات الفارغة",
            callback_data=f"smmp:cat:{category_id}:1", style="success",
        )
    b.button(text="🔄 تحديث", callback_data=f"smmp:cat:{category_id}:{1 if only_with_products else 0}", style="success")
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(1)
    return b.as_markup()


# ══════════════ الأقسام الفرعية داخل تطبيق ══════════════


def smm_sections_kb(
    app_id: int,
    category_id: int,
    rows,
    *,
    direct_products: int,
) -> InlineKeyboardMarkup:
    """أقسام التطبيق الفرعية + منتجات التطبيق المباشرة + أدوات جماعية."""
    b = InlineKeyboardBuilder()
    for stats in rows:
        b.button(
            text=f"{_short(stats.label, 26)} · {stats.total} منتج ({stats.active} ✅)",
            callback_data=f"smmp:sec:{stats.sub.id}:0", style="success",
        )
    if direct_products:
        b.button(
            text=f"📦 منتجات التطبيق المباشرة ({direct_products})",
            callback_data=f"smmp:sec:{app_id}:0", style="success",
        )
    b.button(
        text="💵 نسبة ربح كل منتجات التطبيق",
        callback_data=f"smmp:margin:{app_id}",
        style="primary",
    )
    b.button(text="🟢 تفعيل كل منتجات التطبيق", callback_data=f"smmp:on:{app_id}", style="success")
    b.button(text="⚪ تعطيل كل منتجات التطبيق", callback_data=f"smmp:off:{app_id}", style="success")
    b.button(text="🔄 تحديث", callback_data=f"smmp:app:{app_id}", style="success")
    b.button(text="🔙 التطبيقات", callback_data=f"smmp:cat:{category_id}:1")
    b.adjust(1)
    return b.as_markup()


# ══════════════ منتجات قسم فرعي ══════════════


def smm_products_kb(
    sub_id: int,
    products,
    *,
    page: int,
    pages: int,
    parent_id: int | None,
    category_id: int,
) -> InlineKeyboardMarkup:
    """لكل منتج صفّان من الأزرار: (تعطيل/تفعيل) + (حذف)."""
    b = InlineKeyboardBuilder()
    layout: list[int] = []
    for product in products:
        active = getattr(product.status, "value", product.status) == "active"
        mark = "🟢" if active else "⚪"
        b.button(
            text=f"{mark} {_short(product.name_ar, 24)} · {_price(product.price_usd)}$",
            callback_data=f"smmp:tg:{product.id}:{page}", style="success",
        )
        b.button(text="🗑", callback_data=f"smmp:del:{product.id}:{page}", style="danger")
        layout.append(2)

    nav = 0
    if page > 0:
        b.button(text="◀️ السابق", callback_data=f"smmp:sec:{sub_id}:{page - 1}")
        nav += 1
    if page < pages - 1:
        b.button(text="التالي ▶️", callback_data=f"smmp:sec:{sub_id}:{page + 1}")
        nav += 1
    if nav:
        layout.append(nav)

    b.button(
        text="💵 نسبة الربح لكل منتجات هذا القسم",
        callback_data=f"smmp:margin:{sub_id}",
        style="primary",
    )
    b.button(text="🟢 تفعيل كل منتجات القسم", callback_data=f"smmp:on:{sub_id}", style="success")
    b.button(text="⚪ تعطيل كل منتجات القسم", callback_data=f"smmp:off:{sub_id}", style="success")
    b.button(
        text="🧹 حذف كل منتجات القسم",
        callback_data=f"smmp:sec_delete:{sub_id}",
        style="danger",
    )
    b.button(text="🔄 تحديث", callback_data=f"smmp:sec:{sub_id}:{page}", style="success")
    layout.extend([1, 2, 1, 1])

    if parent_id:
        b.button(text="🔙 أقسام التطبيق", callback_data=f"smmp:app:{parent_id}")
    else:
        b.button(text="🔙 التطبيقات", callback_data=f"smmp:cat:{category_id}:1")
    layout.append(1)

    b.adjust(*layout)
    return b.as_markup()


# ══════════════ تأكيدات ══════════════


def confirm_delete_product_kb(product_id: int, sub_id: int, page: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text="🗑 نعم، احذف المنتج",
        callback_data=f"smmp:delete_go:{product_id}:{page}",
        style="danger",
    )
    b.button(text="↩️ تراجع", callback_data=f"smmp:sec:{sub_id}:{page}", style="success")
    b.adjust(1)
    return b.as_markup()


def confirm_purge_section_kb(sub_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text="🧹 نعم، احذف كل المنتجات",
        callback_data=f"smmp:sec_delete_go:{sub_id}",
        style="danger",
    )
    b.button(text="↩️ تراجع", callback_data=f"smmp:sec:{sub_id}:0", style="success")
    b.adjust(1)
    return b.as_markup()


def margin_cancel_kb(sub_id: int, *, is_app: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text="↩️ إلغاء",
        callback_data=(f"smmp:app:{sub_id}" if is_app else f"smmp:sec:{sub_id}:0"),
    )
    b.adjust(1)
    return b.as_markup()
