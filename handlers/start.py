"""
أوامر البداية والقائمة الرئيسية، ومعالجة روابط الشراء السريعة القادمة من القناة العامة.
"""

from decimal import Decimal
from html import escape

from aiogram import Router, F
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from keyboards.main_menu import build_main_menu
from keyboards.common import check_subscription_kb
from services.referral_guard_service import ReferralGuardService
from keyboards.numbers import confirm_purchase_kb
from providers.countries import get_country_by_code, get_number_service_by_code
from providers.manager import provider_manager
from services.currency_service import CurrencyService
from services.subscription_service import SubscriptionService
from services.settings_service import SettingsService
from services.pricing_service import PricingService
from services.price_lock_service import PriceLockService
from services.i18n_service import I18nService

router = Router(name="start")


async def _build_menu(session, db_user):
    """بناء القائمة الرئيسية بلغة وعملة المستخدم."""
    from services.feature_service import FeatureService

    language = db_user.language_code
    balance_display = await CurrencyService.format_user_amount(
        db_user.balance, db_user, session
    )
    show_agent = await FeatureService.enabled("agent_program")
    agent_percent = str(
        await FeatureService.config("agent_program", "default_percent", 10)
    )
    completed_orders = await _completed_orders_count(session)
    show_ai = await _ai_section_visible(session)
    show_whatsapp = await FeatureService.enabled("whatsapp_section")
    return build_main_menu(
        number_services=[],
        categories=[],
        balance_usd=f"{db_user.balance:.2f}",
        language=language,
        balance_display=balance_display,
        show_agent=show_agent,
        agent_percent=agent_percent,
        completed_orders_count=completed_orders,
        show_ai=show_ai,
        show_whatsapp=show_whatsapp,
    )


async def _ai_section_visible(session) -> bool:
    """زر الذكاء الاصطناعي يظهر إذا فُعّلت الميزة وفيه قسم مفعّل."""
    from sqlalchemy import func, select

    from database.models import AiSection
    from services.feature_service import FeatureService

    if not await FeatureService.enabled("ai_sections"):
        return False
    count = await session.scalar(
        select(func.count(AiSection.id)).where(AiSection.enabled.is_(True))
    )
    return bool(count)


async def _completed_orders_count(session) -> int:
    """إجمالي الطلبات المنجزة لزر الإنجازات في القائمة الرئيسية."""
    from sqlalchemy import func, select
    from database.models import NumberOrder, OrderStatus, UnifiedOrder, UnifiedOrderStatus

    number_count = (
        await session.execute(
            select(func.count(NumberOrder.id)).where(NumberOrder.status == OrderStatus.COMPLETED)
        )
    ).scalar_one()
    unified_count = (
        await session.execute(
            select(func.count(UnifiedOrder.id)).where(UnifiedOrder.status == UnifiedOrderStatus.COMPLETED)
        )
    ).scalar_one()
    return int(number_count or 0) + int(unified_count or 0)


async def _main_header(session, db_user) -> str:
    """رأس القائمة الرئيسية مع عرض الرصيد بالدولار وما يعادله بالعملة المحلية."""
    balance_text = await CurrencyService.format_dual(db_user.balance, db_user, session)
    return I18nService.t(
        "main_menu_header",
        db_user.language_code,
        balance=balance_text,
    )


async def _alternatives_kb(session, service, missing_code: str):
    """بدائل متاحة الآن عندما تنفد الدولة القادمة من قناة التوفر."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    from services.number_catalog_service import build_board, format_price

    buttons: list[list[InlineKeyboardButton]] = []
    try:
        entries = await build_board(session, service, use_cache=True)
    except Exception:  # noqa: BLE001
        entries = []
    for entry in entries:
        if str(entry.code).lower() == str(missing_code).lower():
            continue
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"🟢 {entry.flag} {entry.name_ar} — {format_price(entry.sell_usd)}$",
                    callback_data=f"num_country:{service.code}:{entry.code}", style="success",
                )
            ]
        )
        if len(buttons) >= 5:
            break
    buttons.append(
        [InlineKeyboardButton(text="🔄 كل الدول المتاحة", callback_data=f"num_svc:{service.code}", style="success")]
    )
    buttons.append([InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, session, db_user, state: FSMContext):
    # 1. التحقق من الاشتراك الإجباري بالقنوات
    is_ok, missing = await SubscriptionService.is_user_subscribed_all(
        message.bot, session, db_user.telegram_id
    )

    if not is_ok and not db_user.is_admin:
        await message.answer(
            I18nService.t("subscribe_first", db_user.language_code),
            reply_markup=check_subscription_kb(missing),
        )
        return

    # 1.5 التحقق البشري لروابط الإحالة (حماية من البوتات):
    # لا يُفعَّل الحساب ولا تُدفع مكافأة المحيل قبل نجاح الاختبار.
    if await ReferralGuardService.requires_check(db_user):
        from handlers.referral_guard import send_human_check

        await send_human_check(message, state, db_user)
        return
    if db_user.referral_check_pending:
        # الميزة معطلة حالياً → نلغي الانتظار ونتابع كالمعتاد.
        db_user.referral_check_pending = False
        await session.commit()

    # 2. تفعيل المستخدم ومكافأة الإحالة
    if not db_user.is_activated:
        db_user.is_activated = True
        await session.commit()
        await _try_pay_referral_bonus(session, db_user, message.bot)

    # 3. معالجة روابط المنتجات/الشراء السريع القادمة من inline أو قناة التوفر
    args = command.args
    if args and args.startswith("prod_"):
        try:
            product_id = int(args.replace("prod_", "", 1))
        except ValueError:
            product_id = 0
        if product_id:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
            from database.models import ProductStatus
            from services.dynamic_service import DynamicService

            product = await DynamicService.get_product(session, product_id)
            if product and product.status == ProductStatus.ACTIVE:
                price_display = await CurrencyService.format_dual(product.price_usd, db_user, session)
                await message.answer(
                    f"🛍 <b>{escape(product.name_ar)}</b>\n\n"
                    f"{escape(product.description or 'خدمة رقمية جاهزة للطلب')}\n\n"
                    f"💰 <b>السعر:</b> {price_display}\n\n"
                    "اضغط شراء للانتقال لخطوات الطلب داخل البوت:",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="🛒 شراء الآن", callback_data=f"prod:{product.id}", style="success")],
                            [InlineKeyboardButton(text="🟢 🛍 المتجر", callback_data="store:home", style="success")],
                        ]
                    ),
                )
                return

    if args and args.startswith("buy_"):
        raw_payload = args.replace("buy_", "", 1)
        if "__" in raw_payload:
            service_code, country_code = raw_payload.split("__", 1)
            service = await get_number_service_by_code(session, service_code)
            country = await get_country_by_code(session, country_code)

            if service and country and country.is_active:
                prices = await provider_manager.get_cheapest_price(service, country, session)
                from services.country_localization_service import display_flag, display_name

                if not prices:
                    # التوفر المتقطع: الرقم قد ينفد بين نشر اللوحة وضغط الزر.
                    # بدل رسالة مسدودة نعرض بدائل متاحة الآن فوراً.
                    await message.answer(
                        I18nService.t(
                            "no_numbers_available",
                            db_user.language_code,
                            country=f"{display_flag(country)} {display_name(country)}",
                            service=service.name_ar,
                        ),
                        reply_markup=await _alternatives_kb(session, service, country.code),
                    )
                    return
                cheapest_provider = min(prices, key=prices.get)
                cost_usd = prices[cheapest_provider]
                sell_price = await PricingService.calculate_sell_price(
                    session, service_code, country_code, cheapest_provider, cost_usd
                )
                quote = await PriceLockService.create(
                    service_code, country_code, cheapest_provider.value, cost_usd, sell_price
                )

                price_display = await CurrencyService.format_dual(sell_price, db_user, session)
                await message.answer(
                    f"⚡ <b>طلب رقم سريع من القناة العامة:</b>\n\n"
                    f"🌍 <b>الدولة:</b> {display_flag(country)} {display_name(country)}\n"
                    f"{service.emoji} <b>الخدمة:</b> {service.name_ar}\n"
                    f"💰 <b>السعر:</b> <b>{price_display}</b>\n\n"
                    "🛡 <b>الضمان:</b> استرجاع تلقائي في حال لم يصل الكود.\n\n"
                    "اضغط على الزر أدناه لإتمام الشراء فوراً:",
                    reply_markup=confirm_purchase_kb(service_code, country_code, quote.token),
                )
                return

    # 4. رسالة الترحيب الافتراضية
    default_name = "friend" if db_user.language_code == "en" else "عزيزي"
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
    """دفع مكافأة الإحالة."""
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
        description=f"مكافأة إحالة عن المستخدم {user.telegram_id}",
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
async def check_subscription_callback(
    callback: CallbackQuery,
    session,
    db_user,
    bot,
    state: FSMContext,
):
    """التحقق من الاشتراك بالقنوات الإجبارية."""
    is_ok, missing = await SubscriptionService.is_user_subscribed_all(
        bot, session, db_user.telegram_id
    )

    if is_ok:
        # التحقق البشري أولاً لمن دخل عبر رابط إحالة.
        if await ReferralGuardService.requires_check(db_user):
            await callback.answer()
            from handlers.referral_guard import send_human_check

            await send_human_check(callback.message, state, db_user)
            return
        if db_user.referral_check_pending:
            # الميزة معطلة حالياً → نلغي الانتظار.
            db_user.referral_check_pending = False
            await session.commit()
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