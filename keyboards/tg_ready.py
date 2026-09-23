"""أزرار قسم الجلسات الجاهزة — أدمن + زبون."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def tg_ready_countries_kb(countries: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in countries:
        b.button(
            text=f"{c['flag']} {c['name']} — {c['price']}$ ({c['stock']})",
            callback_data=f"tgready:country:{c['key']}",
            style="success",
        )
    b.button(text="🏠 القائمة الرئيسية", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def tg_ready_confirm_kb(country_key: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(
        text="✅ تأكيد الشراء الآن",
        callback_data=f"tgready:buy:{country_key}",
        style="primary",
    )
    b.button(text="🔙 رجوع للدول", callback_data="tgready:list")
    b.adjust(1)
    return b.as_markup()


def tg_ready_after_kb(item_id: int | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if item_id:
        b.button(
            text="📩 طلب الكود",
            callback_data=f"tgready:code:{item_id}",
            style="success",
        )
    b.button(text="🔄 شراء رقم آخر", callback_data="tgready:list", style="success")
    b.button(text="🏠 القائمة الرئيسية", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def tg_ready_owned_kb(item_id: int) -> InlineKeyboardMarkup:
    """أزرار مالك الرقم: طلب الكود + شراء آخر (بدون تسليم ملف)."""
    return tg_ready_after_kb(item_id)


def tg_ready_entry_kb(total: int) -> InlineKeyboardMarkup:
    """زر الدخول للقسم الجاهز من داخل صفحة تلجرام OTP."""
    b = InlineKeyboardBuilder()
    if total > 0:
        b.button(
            text=f"📦 أرقام تلجرام — جلسات (متاح {total})",
            callback_data="tgready:list",
            style="success",
        )
    return b.as_markup()


def admin_tg_ready_kb(
    countries: list[dict], total: int, margin: str | int | float
) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📤 رفع ملف أرقام جديد", callback_data="admin:tg_ready_upload")
    b.button(text="إضافة الكل", callback_data="admin:tg_ready_upload_all")
    b.button(text=f"💰 نسبة الربح الحالية: {margin}% (تغيير)", callback_data="admin:tg_ready_margin")
    for c in countries:
        b.button(
            text=f"{c['flag']} {c['name']} — {c['stock']}",
            callback_data=f"admin:tg_ready_country:{c['key']}",
        )
    b.button(text="🗑 تصفير كل المخزون", callback_data="admin:tg_ready_wipe_ask")
    b.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    b.adjust(2, 1)
    return b.as_markup()


def admin_tg_ready_country_kb(country_key: str, is_active: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💰 تعديل السعر", callback_data=f"admin:tg_ready_price:{country_key}")
    if is_active:
        b.button(text="⚪ إخفاء عن الزبائن", callback_data=f"admin:tg_ready_toggle:{country_key}")
    else:
        b.button(text="🟢 إظهار للزبائن", callback_data=f"admin:tg_ready_toggle:{country_key}")
    b.button(text="🗑 حذف مخزون الدولة", callback_data=f"admin:tg_ready_del:{country_key}")
    b.button(text="🔙 المخزون", callback_data="admin:tg_ready")
    b.adjust(1)
    return b.as_markup()


def admin_tg_ready_wipe_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🗑 نعم، احذف كل المخزون", callback_data="admin:tg_ready_wipe_yes")
    b.button(text="❌ تراجع", callback_data="admin:tg_ready")
    b.adjust(1)
    return b.as_markup()
