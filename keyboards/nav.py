"""أدوات تنقل موحدة: زر رجوع يعود للصفحة السابقة فعلاً.

القاعدة:
- داخل دول سيرفر محدد → الرجوع لدول نفس السيرفر (num_server_pick)
- داخل دول خدمة (بلا سيرفر) → الرجوع لقائمة السيرفرات أو الخدمات
- رسائل الفشل → نفس وجهة الرجوع + زر رئيسية
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def server_countries_cb(service_code: str, server_id: int | None) -> str:
    """وجهة «دول السيرفر/الخدمة» السابقة."""
    if server_id:
        return f"num_server_pick:{service_code}:{server_id}"
    return f"num_server:{service_code}"


def number_failure_kb(
    service_code: str,
    country_ref: str | int | None = None,
    server_id: int | None = None,
) -> InlineKeyboardMarkup:
    """لوحة رسائل فشل الشراء/نفاد المخزون.

    الزر الأول يرجع لدول نفس السيرفر المختار (وليس لقائمة السيرفرات)،
    والثاني يتيح تغيير السيرفر، والثالث رئيسية.
    """
    b = InlineKeyboardBuilder()
    b.button(
        text="🔙 رجوع لدول السيرفر" if server_id else "🔙 رجوع للدول",
        callback_data=server_countries_cb(service_code, server_id),
    )
    if server_id:
        b.button(
            text="🖥 تغيير السيرفر",
            callback_data=f"num_server:{service_code}",
        )
    else:
        b.button(
            text="📱 كل الخدمات",
            callback_data="num_hub",
        )
    b.button(text="🏠 القائمة الرئيسية", callback_data="back_to_main")
    b.adjust(1)
    return b.as_markup()


def back_to_admin_kb(back_callback: str = "admin:main") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 رجوع", callback_data=back_callback)
    return b.as_markup()
