"""أزرار قسم الجلسات الجاهزة (أرقام تلجرام الجاهزة) — أدمن + زبون."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def _price(p) -> str:
    from decimal import Decimal

    v = Decimal(str(p or 0))
    return f"{v:.2f}"


# ── الزبون ──


def tg_ready_countries_kb(countries: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in countries:
        b.button(
            text=f"{c['flag']} {c['name']} — {_price(c['price'])}$ (متاح {c['stock']})",
            callback_data=f"tgready:country:{c['key']}",
        )
    b.button(text="🔙 رجوع لأرقام تلجرام", callback_data="num_svc:telegram")
    b.adjust(1)
    return b.as_markup()


def tg_ready_confirm_kb(country_key: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ تأكيد الشراء الآن", callback_data=f"tgready:buy:{country_key}")
    b.button(text="🔙 رجوع للدول", callback_data="tgready:list")
    b.adjust(1)
    return b.as_markup()


def tg_ready_after_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔄 شراء رقم آخر", callback_data="tgready:list")
    b.button(text="🏠 القائمة الرئيسية", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def tg_ready_entry_kb(total: int) -> InlineKeyboardMarkup:
    """زر الدخول للقسم الجاهز من داخل صفحة تلجرام OTP."""
    b = InlineKeyboardBuilder()
    b.button(
        text=f"📦 حسابات جاهزة — جلسات (متاح {total})",
        callback_data="tgready:list",
    )
    b.adjust(1)
    return b.as_markup()


# ── الأدمن ──


def admin_tg_ready_kb(
    countries: list[dict], total: int, margin: str
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📤 رفع ملف أرقام جديد", callback_data="admin:tg_ready_upload")
    b.button(text=f"💰 نسبة الربح الحالية: {margin}% (تغيير)", callback_data="admin:tg_ready_margin")
    for c in countries[:30]:
        b.button(
            text=f"{c['flag']} {c['name']} · {_price(c['price'])}$ · مخزون {c['stock']}",
            callback_data=f"admin:tg_ready_country:{c['key']}",
        )
    b.button(text="🗑 تصفير كل المخزون", callback_data="admin:tg_ready_wipe_ask")
    b.button(text="🔙 رجوع", callback_data="admin:number_services")
    b.adjust(1)
    return b.as_markup()


def admin_tg_ready_country_kb(country_key: str, is_active: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💰 تعديل السعر", callback_data=f"admin:tg_ready_price:{country_key}")
    if is_active:
        b.button(text="⚪ إخفاء عن الزبائن", callback_data=f"admin:tg_ready_toggle:{country_key}")
    else:
        b.button(text="🟢 إظهار للزبائن", callback_data=f"admin:tg_ready_toggle:{country_key}")
    b.button(text=f"🗑 حذف مخزون الدولة", callback_data=f"admin:tg_ready_del:{country_key}")
    b.button(text="🔙 المخزون", callback_data="admin:tg_ready")
    b.adjust(1)
    return b.as_markup()


def admin_tg_ready_wipe_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 نعم، احذف كل المخزون", callback_data="admin:tg_ready_wipe_yes")
    b.button(text="❌ تراجع", callback_data="admin:tg_ready")
    b.adjust(1)
    return b.as_markup()
