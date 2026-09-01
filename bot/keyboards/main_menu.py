"""Compact top-level menu for the bot.

The store and extras pages own their dynamic catalog and feature shortcuts;
the first screen contains only the essential actions and two entry points.
All visible static labels use the existing Arabic/English translation service.
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

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
    """Build the compact top-level menu.

    Product categories and number services are intentionally not rendered here.
    They are all reachable from ``store:home``. Optional features and admin-made
    shortcuts are reachable from ``extras:home`` so the main menu stays short.
    The arguments remain for backwards compatibility with callers that already
    build the menu with dynamic data.
    """
    del number_services, categories, show_marketplace, show_tasks, show_points, dynamic_buttons

    t = lambda key, **kw: I18nService.t(key, language, **kw)  # noqa: E731
    balance_text = balance_display if balance_display is not None else f"${balance_usd}"
    b = InlineKeyboardBuilder()

    # Keep only the essential actions in the first screen.
    b.button(text=t("menu_full_store"), callback_data="store:home")
    b.button(text=t("menu_account_with_balance", balance=balance_text), callback_data="menu:account")
    b.button(text=t("menu_deposit"), callback_data="menu:deposit")
    b.button(text=t("menu_support"), callback_data="menu:support")
    b.button(text=t("menu_referral"), callback_data="menu:referral")
    b.button(text=t("menu_language"), callback_data="menu:language")
    b.button(text=t("menu_currency"), callback_data="menu:currency")
    b.button(text=t("menu_bot_info"), callback_data="info:home")
    b.button(text=t("menu_transfer"), callback_data="menu:transfer")
    b.button(text=t("menu_extras"), callback_data="extras:home")

    b.adjust(2, 2, 2, 2, 2)
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