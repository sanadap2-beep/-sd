"""
أوامر البداية والقائمة الرئيسية.
"""

from decimal import Decimal

from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery

from keyboards.main_menu import build_main_menu
from keyboards.common import check_subscription_kb
from services.currency_service import CurrencyService
from services.feature_service import FeatureService
from services.points_service import PointsService
from services.subscription_service import SubscriptionService
from services.settings_service import SettingsService
from services.dynamic_service import DynamicService
from services.i18n_service import I18nService
from services.main_button_service import MainButtonService
from providers.countries import get_active_number_services

router = Router(name="start")


async def _build_menu(session, db_user):
    """يبني القائمة الرئيسية الديناميكية بلغة المستخدم وعملة عرضه."""
    number_services = await get_active_number_services(session)
    categories = await DynamicService.get_active_categories(session)
    dynamic_buttons = await MainButtonService.list_buttons(include_inactive=False)
    language = db_user.language_code
    balance_display = await CurrencyService.format_user_amount(
        db_user.balance, db_user, session
    )
    # الأزرار الجديدة تظهر فقط حين يفعّلها الأدمن من «مركز الإضافات».
    show_marketplace = await FeatureService.enabled("peer_marketplace")
    show_tasks = await FeatureService.enabled("tasks_system")
    show_points = await PointsService.enabled()

    return build_main_menu(
        number_services=number_services,
        categories=categories,
        balance_usd=f"{db_user.balance:.2f}",
        language=language,
        balance_display=balance_display,
        show_marketplace=show_marketplace,
        show_tasks=show_tasks,
        show_points=show_points,
        dynamic_buttons=dynamic_buttons,
    )


async def _main_header(session, db_user) -> str:
    """سطر القائمة الرئيسية مع الرصيد بالدولار وما يعادله بعملة العرض."""
    balance_text = await CurrencyService.format_dual(db_user.balance, db_user, session)
    return I18nService.t(
        "main_menu_header",
        db_user.language_code,
        balance=balance_text,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, session, db_user):
    is_ok, missing = await SubscriptionService.is_user_subscribed_all(
        message.bot, session, db_user.telegram_id
    )

    if not is_ok and not db_user.is_admin:
        await message.answer(
            I18nService.t("subscribe_first", db_user.language_code),
            reply_markup=check_subscription_kb(missing),
        )
        return

    if not db_user.is_activated:
        db_user.is_activated = True
        await session.commit()
        await _try_pay_referral_bonus(session, db_user, message.bot)

    default_name = (
        "friend" if db_user.language_code == "en" else "عزيزي"
    )
    welcome_msg = await SettingsService.get(
        "welcome_message",
        I18nService.t(
            "welcome",
            db_user.language_code,
            name=message.from_user.full_name or default_name,
        ),
    )
    welcome_msg = welcome_msg.replace(
        "{name}", message.from_user.full_name or default_name
    )

    menu_kb = await _build_menu(session, db_user)
    await message.answer(welcome_msg, reply_markup=menu_kb)


@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, session, db_user):
    """زر الرجوع للقائمة الرئيسية."""
    await callback.answer()
    menu_kb = await _build_menu(session, db_user)
    header = await _main_header(session, db_user)
    try:
        await callback.message.edit_text(header, reply_markup=menu_kb)
    except Exception:
        await callback.message.answer(header, reply_markup=menu_kb)


async def _try_pay_referral_bonus(session, user, bot):
    """يدفع مكافأة الإحالة بالدولار."""
    from database.models import User, TransactionType
    from services.balance_service import BalanceService
    from services.notification_service import NotificationService

    if user.referrer_id is None or user.referral_bonus_paid:
        return

    require_sub = await SettingsService.get_bool("require_subscription_for_referral", True)
    if require_sub and not user.is_activated:
        return

    referrer = await session.get(User, user.referrer_id)
    if referrer is None:
        return

    bonus_usd = await SettingsService.get_decimal("referral_bonus_usd", Decimal("0.015"))

    if bonus_usd <= 0:
        return

    await BalanceService.add_balance(
        session,
        referrer.id,
        bonus_usd,
        TransactionType.REFERRAL_BONUS,
        description=(f"مكافأة إحالة عن المستخدم {user.telegram_id}"),
    )
    user.referral_bonus_paid = True
    await session.commit()

    notifier = NotificationService(bot)
    await notifier.notify_user(
        referrer.telegram_id,
        I18nService.t(
            "referral_bonus_notification",
            referrer.language_code,
            amount=f"{bonus_usd}",
        ),
    )


@router.callback_query(F.data == "check_subscription")
async def check_subscription_callback(callback: CallbackQuery, session, db_user, bot):
    is_ok, missing = await SubscriptionService.is_user_subscribed_all(
        bot, session, db_user.telegram_id
    )

    if is_ok:
        if not db_user.is_activated:
            db_user.is_activated = True
            await session.commit()
            await _try_pay_referral_bonus(session, db_user, bot)

        await callback.message.edit_text(
            I18nService.t("subscription_verified", db_user.language_code)
        )
        menu_kb = await _build_menu(session, db_user)
        header = await _main_header(session, db_user)
        await callback.message.answer(header, reply_markup=menu_kb)
    else:
        await callback.answer(
            I18nService.t("subscription_still_missing", db_user.language_code),
            show_alert=True,
        )
