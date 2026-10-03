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


def flow_cancel_kb(language: str = "ar") -> InlineKeyboardMarkup:
    """زر إلغاء لشاشات الإدخال: يُنهي الحالة ويرجع للقائمة الرئيسية."""
    from services.i18n_service import I18nService

    b = InlineKeyboardBuilder()
    b.button(
        text=I18nService.t("cancel", language),
        callback_data="flow:cancel",
        style="danger",
    )
    return b.as_markup()


def flow_cancel_kb(language: str = "ar") -> InlineKeyboardMarkup:
    """زر إلغاء لشاشات الإدخال: يُنهي الحالة ويرجع للقائمة الرئيسية."""
    from services.i18n_service import I18nService

    b = InlineKeyboardBuilder()
    b.button(
        text=I18nService.t("cancel", language),
        callback_data="flow:cancel",
        style="danger",
    )
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


def empty_state_kb(
    language: str = "ar",
    *,
    back_callback: str = "store:home",
    back_label: str | None = None,
    main_menu: bool = True,
    search: bool = True,
) -> InlineKeyboardMarkup:
    """أزرار الشاشات الفارغة (لا منتجات / لا طلبات).

    شاشة فارغة بلا أزرار = طريق مسدود: الزبون يبقى في رسالة ميتة بلا رجوع.
    هذه اللوحة تعطيه دائماً مخرجاً للخلف + بحث + القائمة الرئيسية.
    """
    from services.i18n_service import I18nService

    b = InlineKeyboardBuilder()
    if search:
        b.button(
            text=I18nService.t("store_search", language),
            callback_data="menu:search",
            style="success",
        )
    b.button(
        text=back_label or I18nService.t("store_back", language),
        callback_data=back_callback,
        style="success",
    )
    if main_menu:
        b.button(
            text=I18nService.t("back_to_main", language),
            callback_data="back_to_main",
        )
    return b.as_markup()
