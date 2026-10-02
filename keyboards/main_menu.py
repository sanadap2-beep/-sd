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
    show_agent: bool = False,
    agent_percent: str = "10",
    completed_orders_count: int | None = None,
    show_ai: bool = False,
    show_whatsapp: bool = False,
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
    from services.button_customization_service import ButtonCustomizationService as BC

    rows: list[list] = []

    # زر «المتجر» واسع وممتد أول الشاشة — نقطة الدخول الأساسية.
    rows.append([BC.apply("main.store", t("menu_full_store"), "store:home", "success")])

    rows.append([
        BC.apply("main.account", t("menu_account_with_balance", balance=balance_text), "menu:account", "primary"),
        BC.apply("main.deposit", t("menu_deposit"), "menu:deposit", "primary"),
    ])
    rows.append([
        BC.apply("main.referral", t("menu_referral"), "menu:referral", "primary"),
        BC.apply("main.transfer", t("menu_transfer"), "menu:transfer", "primary"),
    ])

    row4 = []
    if completed_orders_count is not None:
        row4.append(BC.apply("main.completed_orders", t("menu_completed_orders", count=completed_orders_count), "info:stats", "success"))
    row4.append(BC.apply("main.extras", t("menu_extras"), "extras:home", "success"))
    rows.append(row4)

    if show_ai or show_whatsapp:
        row5 = []
        if show_ai:
            row5.append(BC.apply("main.ai", t("menu_ai"), "ai:home", "success"))
        if show_whatsapp:
            row5.append(BC.apply("main.whatsapp", t("menu_whatsapp"), "wa:home", "success"))
        rows.append(row5)

    if show_agent:
        rows.append([BC.apply("main.agent", t("menu_agent", percent=agent_percent), "agent:home", "primary")])

    rows.append([
        BC.apply("main.support", t("menu_support"), "menu:support", None),
        BC.apply("main.terms", t("menu_terms"), "info:terms", "danger"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def deposit_menu_kb(
    shamcash_manual_enabled: bool = True,
    stars_enabled: bool = True,
    usdt_manual_enabled: bool = True,
    shamcash_auto_enabled: bool = True,
    usdt_auto_enabled: bool = True,
    mobile_credit_enabled: bool = False,
    other_enabled: bool = True,
    language: str = "ar",
) -> InlineKeyboardMarkup:
    """
    قائمة طرق شحن الرصيد (حتى 7 طرق).
    كل طريقة تظهر فقط إذا كانت مفعلة من لوحة الأدمن.
    """
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    b = InlineKeyboardBuilder()
    from services.button_customization_service import ButtonCustomizationService as BC

    if shamcash_manual_enabled:
        b.add(BC.apply("deposit.shamcash_manual", t("deposit_method_shamcash_manual"), "deposit:shamcash_manual", "primary"))

    if stars_enabled:
        b.add(BC.apply("deposit.stars", t("deposit_method_stars"), "deposit:stars", "primary"))

    if shamcash_auto_enabled:
        b.add(BC.apply("deposit.shamcash_auto", t("deposit_method_shamcash_auto"), "deposit:shamcash_auto", "primary"))

    if usdt_auto_enabled:
        b.add(BC.apply("deposit.usdt_auto", t("deposit_method_usdt_auto"), "deposit:usdt_auto", "primary"))

    if usdt_manual_enabled:
        b.add(BC.apply("deposit.usdt_manual", t("deposit_method_usdt_manual"), "deposit:usdt_manual", "primary"))

    if mobile_credit_enabled:
        b.add(BC.apply("deposit.mobile_credit", "📲 رصيد جوال", "deposit:mobile_credit", "primary"))

    if other_enabled:
        b.add(BC.apply("deposit.other", t("deposit_method_other"), "deposit:other", "primary"))

    b.button(
        text=t("back_to_main"),
        callback_data="back_to_main",
    )

    b.adjust(2, 2, 2, 2, 1)
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
            callback_data=f"stars_buy:{pkg.id}", style="primary",
        )
    b.button(
        text=t("back"),
        callback_data="menu:deposit", style="primary",
    )
    b.adjust(2)
    return b.as_markup()


def insufficient_balance_kb(language: str = "ar") -> InlineKeyboardMarkup:
    """زر شحن الرصيد عند عدم كفاية الرصيد."""
    t = lambda key: I18nService.t(key, language)  # noqa: E731
    b = InlineKeyboardBuilder()
    b.button(
        text=t("topup_now"),
        callback_data="menu:deposit", style="primary",
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