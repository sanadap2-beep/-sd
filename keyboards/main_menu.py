"""
القائمة الرئيسية الديناميكية.
تُبنى تلقائياً من:
1) خدمات الأرقام المفعلة (جدول number_services)
2) الأقسام المفعلة (جدول categories)
3) أزرار ثابتة (شحن رصيد، حسابي، إلخ)
كل النصوص الثابتة تمر عبر خدمة الترجمة (عربي/إنجليزي).
"""

from aiogram.types import InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database.models import NumberService, Category
from services.i18n_service import I18nService
from services.main_button_service import MainMenuButton


def build_main_menu(
    number_services: list[NumberService],
    categories: list[Category],
    balance_usd: str = "0.00",
    language: str = "ar",
    balance_display: str | None = None,
    show_marketplace: bool = False,
    show_tasks: bool = False,
    show_points: bool = False,
    dynamic_buttons: list[MainMenuButton] | None = None,
) -> InlineKeyboardMarkup:
    """
    يبني القائمة الرئيسية ديناميكياً.
    number_services: خدمات الأرقام المفعلة من DB
    categories: الأقسام الرئيسية المفعلة من DB
    balance_usd: رصيد المستخدم بالدولار
    language: لغة الواجهة (ar/en)
    balance_display: الرصيد منسقاً بعملة العرض (اختياري)
    """
    t = lambda key, **kw: I18nService.t(key, language, **kw)  # noqa: E731
    b = InlineKeyboardBuilder()

    # ── خدمات الأرقام الديناميكية ──
    for svc in number_services:
        b.button(
            text=f"{svc.emoji} {t('menu_numbers')} {svc.name_ar}",
            callback_data=f"num_svc:{svc.code}",
        )

    # ── الأقسام الرئيسية الديناميكية ──
    for cat in categories:
        b.button(
            text=f"{cat.emoji} {cat.name_ar}",
            callback_data=f"cat:{cat.id}",
        )

    # ── أزرار ثابتة ──
    if settings.WEBAPP_URL:
        b.button(
            text=t("menu_full_store"),
            web_app=WebAppInfo(url=settings.WEBAPP_URL),
        )

    # ── أزرار رئيسية ديناميكية من لوحة الأدمن ──
    dynamic_count = 0
    for button in dynamic_buttons or []:
        if not button.is_active:
            continue
        if button.is_url:
            b.button(text=button.label, url=button.action)
        else:
            b.button(text=button.label, callback_data=button.action)
        dynamic_count += 1
    b.button(
        text=t("menu_deposit"),
        callback_data="menu:deposit",
    )
    balance_text = balance_display if balance_display is not None else f"${balance_usd}"
    b.button(
        text=t("menu_account_with_balance", balance=balance_text),
        callback_data="menu:account",
    )
    b.button(
        text=t("menu_referral"),
        callback_data="menu:referral",
    )
    b.button(
        text=t("menu_transfer"),
        callback_data="menu:transfer",
    )
    b.button(
        text=t("menu_withdraw"),
        callback_data="withdraw:home",
    )
    if show_marketplace:
        b.button(
            text=t("menu_marketplace"),
            callback_data="market:home",
        )
    if show_tasks:
        b.button(
            text=t("menu_tasks"),
            callback_data="tasks:home",
        )
    if show_points:
        b.button(
            text=t("menu_points"),
            callback_data="points:home",
        )
    b.button(
        text=t("menu_search"),
        callback_data="menu:search",
    )
    b.button(
        text=t("menu_favorites"),
        callback_data="menu:favorites",
    )
    b.button(
        text=t("menu_cart"),
        callback_data="menu:cart",
    )
    b.button(
        text=t("menu_loyalty"),
        callback_data="menu:loyalty",
    )
    b.button(
        text=t("menu_promotions"),
        callback_data="menu:promotions",
    )
    b.button(
        text=t("menu_product_request"),
        callback_data="menu:product_request",
    )
    b.button(
        text=t("menu_gift"),
        callback_data="menu:gift",
    )
    b.button(
        text=t("menu_assistant"),
        callback_data="menu:assistant",
    )
    b.button(
        text=t("menu_bot_info"),
        callback_data="info:home",
    )
    b.button(
        text=t("menu_special_offers"),
        callback_data="special:home",
    )
    b.button(
        text=t("menu_my_ads"),
        callback_data="ads:home",
    )
    b.button(
        text=t("menu_notifications"),
        callback_data="notif:home",
    )
    b.button(
        text=t("menu_status"),
        callback_data="menu:status",
    )
    b.button(
        text=t("menu_language"),
        callback_data="menu:language",
    )
    b.button(
        text=t("menu_currency"),
        callback_data="menu:currency",
    )
    b.button(
        text=t("menu_challenges"),
        callback_data="menu:challenges",
    )
    b.button(
        text=t("menu_extras"),
        callback_data="extras:home",
    )
    b.button(
        text=t("menu_support"),
        callback_data="menu:support",
    )

    # ── ترتيب الأزرار ──
    num_svc_count = len(number_services)
    cat_count = len(categories)

    rows = []
    if num_svc_count > 0:
        rows.append(min(num_svc_count, 2))
        if num_svc_count > 2:
            rows.append(min(num_svc_count - 2, 2))
    if cat_count > 0:
        remaining = cat_count
        while remaining > 0:
            rows.append(min(remaining, 2))
            remaining -= 2

    fixed_count = 24 + dynamic_count + (1 if settings.WEBAPP_URL else 0)
    fixed_count += int(show_marketplace) + int(show_tasks) + int(show_points)
    rows.extend([2] * (fixed_count // 2))
    if fixed_count % 2:
        rows.append(1)
    b.adjust(*rows)

    return b.as_markup()


def deposit_menu_kb(
    shamcash_manual_enabled: bool = True,
    stars_enabled: bool = True,
    usdt_manual_enabled: bool = True,
    shamcash_auto_enabled: bool = True,
    usdt_auto_enabled: bool = True,
    other_enabled: bool = True,
    language: str = "ar",
) -> InlineKeyboardMarkup:
    """
    قائمة طرق شحن الرصيد (6 طرق).
    كل طريقة تظهر فقط إذا كانت مفعلة من لوحة الأدمن.
    """
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    b = InlineKeyboardBuilder()

    if shamcash_manual_enabled:
        b.button(
            text=t("deposit_method_shamcash_manual"),
            callback_data="deposit:shamcash_manual",
        )

    if stars_enabled:
        b.button(
            text=t("deposit_method_stars"),
            callback_data="deposit:stars",
        )

    if shamcash_auto_enabled:
        b.button(
            text=t("deposit_method_shamcash_auto"),
            callback_data="deposit:shamcash_auto",
        )

    if usdt_auto_enabled:
        b.button(
            text=t("deposit_method_usdt_auto"),
            callback_data="deposit:usdt_auto",
        )

    if usdt_manual_enabled:
        b.button(
            text=t("deposit_method_usdt_manual"),
            callback_data="deposit:usdt_manual",
        )

    if other_enabled:
        b.button(
            text=t("deposit_method_other"),
            callback_data="deposit:other",
        )

    b.button(
        text=t("back_to_main"),
        callback_data="back_to_main",
    )

    b.adjust(2, 2, 2, 1)
    return b.as_markup()


def stars_packages_kb(
    packages: list,
    language: str = "ar",
) -> InlineKeyboardMarkup:
    """قائمة باقات النجوم المتاحة."""
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    b = InlineKeyboardBuilder()
    for pkg in packages:
        b.button(
            text=f"{pkg.label} = ${pkg.usd_amount}",
            callback_data=f"stars_buy:{pkg.id}",
        )
    b.button(
        text=t("back"),
        callback_data="menu:deposit",
    )
    b.adjust(2)
    return b.as_markup()


def insufficient_balance_kb(language: str = "ar") -> InlineKeyboardMarkup:
    """زر شحن الرصيد عند عدم كفاية الرصيد."""
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    b = InlineKeyboardBuilder()
    b.button(
        text=t("topup_now"),
        callback_data="menu:deposit",
    )
    b.button(
        text=t("back_to_menu"),
        callback_data="back_to_main",
    )
    b.adjust(1)
    return b.as_markup()


def confirm_large_order_kb(
    confirm_data: str,
    language: str = "ar",
) -> InlineKeyboardMarkup:
    """تأكيد الطلبات الكبيرة."""
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    b = InlineKeyboardBuilder()
    b.button(
        text=t("yes_confirm"),
        callback_data=confirm_data,
    )
    b.button(
        text=t("cancel"),
        callback_data="back_to_main",
    )
    b.adjust(2)
    return b.as_markup()


def back_to_main_kb(language: str = "ar") -> InlineKeyboardMarkup:
    """زر رجوع للقائمة الرئيسية فقط."""
    b = InlineKeyboardBuilder()
    b.button(
        text=I18nService.t("back_to_main", language),
        callback_data="back_to_main",
    )
    return b.as_markup()
