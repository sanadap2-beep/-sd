"""
هاندلر شراء الأرقام.
ديناميكي بالكامل: الخدمات والدول تُقرأ من DB.
المزود يُختار تلقائياً (الأرخص).
العملة: دولار (USD).
"""

import json
import logging
from datetime import datetime, timedelta

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select, func

from database.models import (
    NumberOrder,
    OrderStatus,
    ProviderName,
    TransactionType,
    User,
)
from providers.manager import provider_manager, ProviderUnavailableError
from providers.countries import (
    get_active_countries,
    get_active_number_services,
    get_country_by_code,
    get_number_service_by_code,
)
from services.pricing_service import PricingService
from services.currency_service import CurrencyService
from services.i18n_service import I18nService
from services.settings_service import SettingsService
from services.price_lock_service import PriceLockService
from services.balance_service import BalanceService, InsufficientBalanceError
from services.bulk_number_service import BulkError, BulkNumberService
from services.feature_service import FeatureService
from services.notification_service import NotificationService
from keyboards.numbers import (
    bulk_confirm_kb,
    bulk_quantity_kb,
    countries_kb,
    confirm_purchase_kb,
    order_actions_kb,
    ready_number_packages_kb,
)
from keyboards.main_menu import insufficient_balance_kb
from states.states import NumberBulkStates

logger = logging.getLogger(__name__)

router = Router(name="numbers")

DEFAULT_ORDER_TIMEOUT_MINUTES = 5


async def _get_order_timeout() -> int:
    value = await SettingsService.get_int("order_timeout_minutes", DEFAULT_ORDER_TIMEOUT_MINUTES)
    return value


async def _check_rate_limit(session, user_id: int) -> bool:
    """يتحقق من Rate Limiting."""
    from database.models import RateLimitLog

    rate_limit_seconds = await SettingsService.get_int("rate_limit_seconds", 30)
    if rate_limit_seconds <= 0:
        return True

    cutoff = datetime.utcnow() - timedelta(seconds=rate_limit_seconds)
    result = await session.execute(
        select(func.count(RateLimitLog.id)).where(
            RateLimitLog.user_id == user_id,
            RateLimitLog.action == "buy_number",
            RateLimitLog.created_at >= cutoff,
        )
    )
    count = result.scalar_one()
    return count == 0


async def _log_rate_limit(session, user_id: int):
    from database.models import RateLimitLog

    session.add(
        RateLimitLog(
            user_id=user_id,
            action="buy_number",
        )
    )
    await session.commit()


async def _check_active_orders_limit(session, user_id: int) -> bool:
    """يتحقق من حد الطلبات النشطة."""
    max_orders = await SettingsService.get_int("max_active_orders", 3)
    result = await session.execute(
        select(func.count(NumberOrder.id)).where(
            NumberOrder.user_id == user_id,
            NumberOrder.status == OrderStatus.PENDING,
        )
    )
    active_count = result.scalar_one()
    return active_count < max_orders


# ══════════════ اختيار الخدمة ══════════════


@router.callback_query(F.data.startswith("num_svc:"))
async def number_service_selected(callback: CallbackQuery, session, db_user=None):
    language = getattr(db_user, "language_code", "ar") or "ar"
    service_code = callback.data.split(":")[1]
    service = await get_number_service_by_code(session, service_code)
    if service is None or not service.is_active:
        await callback.answer(
            I18nService.t("service_unavailable", language),
            show_alert=True,
        )
        return

    await callback.answer()
    countries = await get_active_countries(session)
    if not countries:
        await callback.message.edit_text(
            I18nService.t("no_countries", language),
        )
        return

    await callback.message.edit_text(
        f"{service.emoji} <b>{I18nService.t('numbers_for', language)} {service.name_ar}</b>\n\n{I18nService.t('choose_country', language)}:",
        reply_markup=countries_kb(service_code, countries),
    )


# ══════════════ Pagination الدول ══════════════


@router.callback_query(F.data.startswith("num_page:"))
async def countries_page(callback: CallbackQuery, session, db_user=None):
    language = getattr(db_user, "language_code", "ar") or "ar"
    parts = callback.data.split(":")
    service_code = parts[1]
    page = int(parts[2])

    service = await get_number_service_by_code(session, service_code)
    if service is None:
        await callback.answer(I18nService.t("number_service_missing", language), show_alert=True)
        return

    await callback.answer()
    countries = await get_active_countries(session)
    await callback.message.edit_text(
        I18nService.t("numbers_page_title", language, emoji=service.emoji, service=service.name_ar),
        reply_markup=countries_kb(service_code, countries, page),
    )


# ══════════════ عرض السعر ══════════════


@router.callback_query(F.data.startswith("num_country:"))
async def show_price(callback: CallbackQuery, session, db_user=None):
    language = getattr(db_user, "language_code", "ar") or "ar"
    parts = callback.data.split(":")
    service_code = parts[1]
    country_code = parts[2]

    service = await get_number_service_by_code(session, service_code)
    country = await get_country_by_code(session, country_code)

    if not service or not country or not country.is_active:
        await callback.answer(
            I18nService.t("service_country_unavailable", language),
            show_alert=True,
        )
        return

    await callback.answer(I18nService.t("fetching_price", language))

    try:
        prices = await provider_manager.get_cheapest_price(service, country, session)
    except Exception as e:
        logger.error(f"خطأ جلب الأسعار: {e}")
        await callback.message.answer(I18nService.t("providers_unreachable", language))
        return

    if not prices:
        await callback.message.answer(
            I18nService.t(
                "no_numbers_available",
                language,
                country=f"{country.flag} {country.name_ar}",
                service=service.name_ar,
            )
        )
        return

    cheapest_provider = min(prices, key=prices.get)
    cost_usd = prices[cheapest_provider]

    margin_type, margin_value = await PricingService.get_margin(
        session, service_code, country_code, cheapest_provider
    )
    sell_price = PricingService.apply_margin(cost_usd, margin_type, margin_value)
    quote = await PriceLockService.create(
        service_code,
        country_code,
        cheapest_provider.value,
        cost_usd,
        sell_price,
    )

    price_display = await CurrencyService.format_dual(sell_price, db_user, session)
    await callback.message.edit_text(
        f"🌍 {I18nService.t('country', language)}: {country.flag} {country.name_ar}\n"
        f"{service.emoji} {I18nService.t('service', language)}: {service.name_ar}\n"
        f"💰 {I18nService.t('price', language)}: <b>{price_display}</b>\n\n"
        f"{I18nService.t('number_guarantee_note', language)}\n\n"
        f"{I18nService.t('confirm_purchase_q', language)}",
        reply_markup=confirm_purchase_kb(service_code, country_code, quote.token),
    )


# ══════════════ الباقات الجاهزة ══════════════


async def _ready_package_quantities() -> list[int]:
    raw = await FeatureService.config("ready_number_packages", "quantities_json", "[5,10,25,50]")
    try:
        values = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        values = [5, 10, 25, 50]
    quantities = []
    for value in values or []:
        try:
            quantity = int(value)
        except (TypeError, ValueError):
            continue
        if quantity >= 2:
            quantities.append(quantity)
    return quantities or [5, 10, 25, 50]


@router.callback_query(F.data == "num_packages")
async def ready_number_packages(callback: CallbackQuery, session, db_user: User):
    if not await FeatureService.enabled("ready_number_packages"):
        await callback.answer(I18nService.t("number_packages_disabled", getattr(db_user, "language_code", "ar") or "ar"), show_alert=True)
        return
    if not await FeatureService.enabled("bulk_numbers"):
        await callback.message.edit_text(
            I18nService.t("number_bulk_requires_feature", getattr(db_user, "language_code", "ar") or "ar"),
        )
        await callback.answer()
        return

    services = [svc for svc in await get_active_number_services(session) if svc.is_active]
    countries = [country for country in await get_active_countries(session) if country.is_active]
    if not services or not countries:
        await callback.message.edit_text(
            I18nService.t("number_packages_empty", getattr(db_user, "language_code", "ar") or "ar"),
        )
        await callback.answer()
        return

    max_cards = await FeatureService.config_int("ready_number_packages", "max_cards", 8)
    quantities = await _ready_package_quantities()
    def has_common_provider(service, country) -> bool:
        for provider in ProviderName:
            if provider_manager._get_provider_code(provider, country) and provider_manager._get_service_code(provider, service):
                return True
        return False

    packages: list[dict] = []
    for service in services[:4]:
        country = next((item for item in countries if has_common_provider(service, item)), None)
        if country is None:
            continue
        for quantity in quantities[:2]:
            packages.append(
                {
                    "label": f"📦 {quantity} رقم {service.name_ar} · {country.flag} {country.name_ar}",
                    "service_code": service.code,
                    "country_code": country.code,
                    "quantity": quantity,
                }
            )
            if len(packages) >= max_cards:
                break
        if len(packages) >= max_cards:
            break

    await callback.message.edit_text(
        I18nService.t("number_packages_title", getattr(db_user, "language_code", "ar") or "ar"),
        reply_markup=ready_number_packages_kb(packages),
    )
    await callback.answer()


# ══════════════ شراء الأرقام بالجملة ══════════════


async def _load_service_country(session, service_code: str, country_code: str):
    service = await get_number_service_by_code(session, service_code)
    country = await get_country_by_code(session, country_code)
    if not service or not country or not country.is_active:
        return None, None
    return service, country


async def _show_bulk_quote(callback_or_message, session, db_user: User, service_code: str, country_code: str, quantity: int):
    language = getattr(db_user, "language_code", "ar") or "ar"
    service, country = await _load_service_country(session, service_code, country_code)
    if service is None or country is None:
        await callback_or_message.answer(I18nService.t("number_service_country_unavailable", language))
        return

    try:
        quote = await BulkNumberService.quote(session, service, country, quantity)
    except BulkError as exc:
        await callback_or_message.answer(f"⚠️ {exc}")
        return

    unit_display = await CurrencyService.format_dual(quote["unit_price_usd"], db_user, session)
    total_display = await CurrencyService.format_dual(quote["total_usd"], db_user, session)
    discount_display = await CurrencyService.format_dual(quote["discount_usd"], db_user, session)

    text = (
        I18nService.t("number_bulk_quote_title", language) + "\n\n"
        f"{service.emoji} {I18nService.t('service', language)}: <b>{service.name_ar}</b>\n"
        f"🌍 {I18nService.t('country', language)}: {country.flag} <b>{country.name_ar}</b>\n"
        f"🔢 {I18nService.t('qty_label', language)}: <b>{quantity}</b>\n"
        f"💵 {I18nService.t('unit_before_bulk', language)}: <b>{unit_display}</b>\n"
        f"🎁 {I18nService.t('bulk_discount', language)}: <b>{quote['discount_percent']}%</b> "
        f"(-{discount_display})\n"
        f"💰 {I18nService.t('total_required', language)}: <b>{total_display}</b>\n\n"
        f"{I18nService.t('number_bulk_refund_note', language)}"
    )
    markup = bulk_confirm_kb(service_code, country_code, quantity)
    if isinstance(callback_or_message, CallbackQuery):
        await callback_or_message.message.edit_text(text, reply_markup=markup)
    else:
        await callback_or_message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("num_bulk_start:"))
async def bulk_start(callback: CallbackQuery, session, db_user: User):
    if not await FeatureService.enabled("bulk_numbers"):
        await callback.answer(
            I18nService.t("number_bulk_feature_disabled", getattr(db_user, "language_code", "ar") or "ar"),
            show_alert=True,
        )
        return

    parts = callback.data.split(":")
    service_code = parts[1]
    country_code = parts[2]
    quote_token = parts[3] if len(parts) > 3 else ""
    service, country = await _load_service_country(session, service_code, country_code)
    if service is None or country is None:
        await callback.answer(I18nService.t("number_unavailable", getattr(db_user, "language_code", "ar") or "ar"), show_alert=True)
        return

    max_qty = await BulkNumberService.max_quantity()
    await callback.answer()
    await callback.message.edit_text(
        I18nService.t(
            "number_bulk_choose_qty",
            getattr(db_user, "language_code", "ar") or "ar",
            emoji=service.emoji,
            service=service.name_ar,
            flag=country.flag,
            country=country.name_ar,
            max_qty=max_qty,
        ),
        reply_markup=bulk_quantity_kb(service_code, country_code, quote_token),
    )


@router.callback_query(F.data.startswith("num_bulk_qty:"))
async def bulk_quantity_selected(callback: CallbackQuery, session, db_user: User):
    parts = callback.data.split(":")
    quantity = int(parts[3])
    await callback.answer(I18nService.t("number_bulk_calculating", getattr(db_user, "language_code", "ar") or "ar"))
    await _show_bulk_quote(callback, session, db_user, parts[1], parts[2], quantity)


@router.callback_query(F.data.startswith("num_bulk_custom:"))
async def bulk_custom_quantity(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    await state.set_state(NumberBulkStates.waiting_quantity)
    await state.update_data(service_code=parts[1], country_code=parts[2])
    await callback.message.answer(I18nService.t("number_bulk_custom_prompt", getattr(callback.from_user, "language_code", "ar") or "ar"))
    await callback.answer()


@router.message(NumberBulkStates.waiting_quantity)
async def bulk_custom_quantity_received(message: Message, state: FSMContext, session, db_user: User):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer(I18nService.t("number_bulk_invalid_qty", getattr(db_user, "language_code", "ar") or "ar"))
        return

    quantity = int(raw)
    data = await state.get_data()
    await state.clear()
    await _show_bulk_quote(
        message,
        session,
        db_user,
        data.get("service_code", ""),
        data.get("country_code", ""),
        quantity,
    )


@router.callback_query(F.data.startswith("num_bulk_confirm:"))
async def bulk_confirm(callback: CallbackQuery, session, db_user: User, bot):
    if not await FeatureService.enabled("bulk_numbers"):
        await callback.answer(I18nService.t("number_bulk_feature_disabled", getattr(db_user, "language_code", "ar") or "ar"), show_alert=True)
        return

    parts = callback.data.split(":")
    service_code = parts[1]
    country_code = parts[2]
    quantity = int(parts[3])
    service, country = await _load_service_country(session, service_code, country_code)
    if service is None or country is None:
        await callback.answer(I18nService.t("number_unavailable", getattr(db_user, "language_code", "ar") or "ar"), show_alert=True)
        return

    try:
        quote = await BulkNumberService.quote(session, service, country, quantity)
    except BulkError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if db_user.balance < quote["total_usd"]:
        notifier = NotificationService(bot)
        await notifier.notify_insufficient_balance(
            user_telegram_id=db_user.telegram_id,
            required_usd=str(quote["total_usd"]),
            current_balance_usd=f"{db_user.balance:.2f}",
            reply_markup=insufficient_balance_kb(),
        )
        return

    await callback.answer(I18nService.t("number_bulk_started", getattr(db_user, "language_code", "ar") or "ar"))
    await callback.message.edit_text(
        I18nService.t(
            "number_bulk_processing",
            getattr(db_user, "language_code", "ar") or "ar",
            quantity=quantity,
        )
    )

    try:
        result = await BulkNumberService.execute(
            session,
            db_user.id,
            service,
            country,
            quantity,
            timeout_minutes=await _get_order_timeout(),
        )
    except BulkError as exc:
        await callback.message.answer(f"⚠️ {exc}")
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("فشل تنفيذ دفعة الأرقام: %s", exc)
        await callback.message.answer(I18nService.t("number_bulk_unexpected", getattr(db_user, "language_code", "ar") or "ar"))
        return

    text = I18nService.t(
        "number_bulk_done",
        getattr(db_user, "language_code", "ar") or "ar",
        requested=result["requested"],
        succeeded=result["succeeded"],
        failed=result["failed"],
        net=result["net_charged_usd"],
        refund=result["refunded_usd"],
    )
    await callback.message.answer(text)

    if result["orders"]:
        csv_data = BulkNumberService.export_csv(result["orders"])
        await bot.send_document(
            chat_id=db_user.telegram_id,
            document=BufferedInputFile(
                csv_data.encode("utf-8-sig"),
                filename=f"bulk_numbers_{service_code}_{country_code}.csv",
            ),
            caption=I18nService.t("number_bulk_csv_caption", getattr(db_user, "language_code", "ar") or "ar"),
        )

    notifier = NotificationService(bot)
    await notifier.notify_admin(
        "📦 <b>دفعة أرقام جديدة</b>\n\n"
        f"👤 المستخدم: {db_user.telegram_id} (@{db_user.username or '-'})\n"
        f"{service.emoji} الخدمة: {service.name_ar}\n"
        f"🌍 الدولة: {country.flag} {country.name_ar}\n"
        f"🔢 المطلوب: {result['requested']} | الناجح: {result['succeeded']} | الفاشل: {result['failed']}\n"
        f"💰 الصافي: {result['net_charged_usd']}$"
    )


# ══════════════ تأكيد الشراء ══════════════


@router.callback_query(F.data.startswith("num_confirm:"))
async def confirm_buy(
    callback: CallbackQuery,
    session,
    db_user: User,
    bot,
):
    parts = callback.data.split(":")
    service_code = parts[1]
    country_code = parts[2]
    quote_token = parts[3] if len(parts) > 3 and parts[3] else None
    payment_reference = f"number_purchase:{quote_token}" if quote_token else None

    # Telegram may deliver the same callback more than once. A consumed quote
    # must never turn a retry into a second paid provider order.
    if payment_reference and await BalanceService.get_transaction_by_reference(
        session, payment_reference
    ):
        await callback.answer(
            I18nService.t(
                "number_purchase_already_processed",
                getattr(db_user, "language_code", "ar") or "ar",
            ),
            show_alert=True,
        )
        return

    service = await get_number_service_by_code(session, service_code)
    country = await get_country_by_code(session, country_code)

    if not service or not country or not country.is_active:
        await callback.answer(I18nService.t("number_unavailable", getattr(db_user, "language_code", "ar") or "ar"), show_alert=True)
        return

    # ── Rate Limiting ──
    can_proceed = await _check_rate_limit(session, db_user.id)
    if not can_proceed:
        rate_seconds = await SettingsService.get_int("rate_limit_seconds", 30)
        await callback.answer(
            I18nService.t("number_rate_limit", getattr(db_user, "language_code", "ar") or "ar", seconds=rate_seconds),
            show_alert=True,
        )
        return

    # ── حد الطلبات النشطة ──
    can_order = await _check_active_orders_limit(session, db_user.id)
    if not can_order:
        max_orders = await SettingsService.get_int("max_active_orders", 3)
        await callback.answer(
            I18nService.t("number_active_limit", getattr(db_user, "language_code", "ar") or "ar", max_orders=max_orders),
            show_alert=True,
        )
        return

    await callback.answer(I18nService.t("number_prepare_order", getattr(db_user, "language_code", "ar") or "ar"))

    # ── جلب الأسعار ──
    try:
        prices = await provider_manager.get_cheapest_price(service, country, session)
    except Exception:
        await callback.message.answer(I18nService.t("number_provider_temp_error", getattr(db_user, "language_code", "ar") or "ar"))
        return

    if not prices:
        await callback.message.answer(I18nService.t("number_no_stock", getattr(db_user, "language_code", "ar") or "ar"))
        return

    cheapest_provider = min(prices, key=prices.get)
    cost_usd = prices[cheapest_provider]
    margin_type, margin_value = await PricingService.get_margin(
        session, service_code, country_code, cheapest_provider
    )
    sell_price = PricingService.apply_margin(cost_usd, margin_type, margin_value)
    quote = await PriceLockService.get(quote_token, service_code, country_code)
    preferred_provider = None
    if quote is not None:
        # Use the short-lived quote shown to the user. If it expired, the
        # freshly fetched price above is used instead.
        sell_price = quote.sell_price_usd
        cost_usd = quote.cost_usd
        try:
            preferred_provider = ProviderName(quote.provider)
        except ValueError:
            preferred_provider = None

    # ── فحص الرصيد ──
    if db_user.balance < sell_price:
        notifier = NotificationService(bot)
        await notifier.notify_insufficient_balance(
            user_telegram_id=db_user.telegram_id,
            required_usd=str(sell_price),
            current_balance_usd=f"{db_user.balance:.2f}",
            reply_markup=insufficient_balance_kb(),
        )
        return

    # ── خصم الرصيد ──
    try:
        await BalanceService.deduct_balance(
            session,
            db_user.id,
            sell_price,
            TransactionType.PURCHASE,
            description=(f"شراء رقم {service.name_ar} - {country.name_ar}"),
            is_purchase=True,
            payment_reference=payment_reference,
        )
    except InsufficientBalanceError:
        await callback.message.answer(
            I18nService.t("number_insufficient", getattr(db_user, "language_code", "ar") or "ar"),
        )
        return

    # ── شراء الرقم ──
    try:
        buy_result = await provider_manager.buy_number(
            service,
            country,
            session,
            preferred_provider=preferred_provider,
        )
    except ProviderUnavailableError:
        await BalanceService.add_balance(
            session,
            db_user.id,
            sell_price,
            TransactionType.REFUND,
            description="استرجاع - فشل الشراء",
            payment_reference=(f"number_refund:{payment_reference}" if payment_reference else None),
        )
        await callback.message.answer(I18nService.t("number_all_providers_empty", getattr(db_user, "language_code", "ar") or "ar"))
        return
    except Exception as e:
        logger.error(f"خطأ غير متوقع بالشراء: {e}")
        await BalanceService.add_balance(
            session,
            db_user.id,
            sell_price,
            TransactionType.REFUND,
            description="استرجاع - خطأ تقني",
            payment_reference=(f"number_refund:{payment_reference}" if payment_reference else None),
        )
        await callback.message.answer(I18nService.t("number_technical_refund", getattr(db_user, "language_code", "ar") or "ar"))
        return

    await PriceLockService.consume(quote_token)

    # ── تسجيل Rate Limit ──
    await _log_rate_limit(session, db_user.id)

    # ── حفظ الطلب ──
    timeout_minutes = await _get_order_timeout()
    expires_at = datetime.utcnow() + timedelta(minutes=timeout_minutes)

    order = NumberOrder(
        user_id=db_user.id,
        provider=buy_result.provider,
        provider_order_id=buy_result.provider_order_id,
        service=service_code,
        country_code=country_code,
        phone_number=buy_result.phone_number,
        price_provider_usd=buy_result.cost_usd,
        price_sell_usd=sell_price,
        status=OrderStatus.PENDING,
        expires_at=expires_at,
    )
    session.add(order)
    await session.commit()
    await session.refresh(order)

    # ── رسالة الانتظار ──
    status_msg = await callback.message.answer(
        I18nService.t(
            "number_wait_message",
            getattr(db_user, "language_code", "ar") or "ar",
            phone=buy_result.phone_number,
            minutes=timeout_minutes,
        ),
        reply_markup=order_actions_kb(order.id),
    )
    order.status_chat_id = status_msg.chat.id
    order.status_message_id = status_msg.message_id
    await session.commit()

    # ── إشعار الأدمن ──
    notifier = NotificationService(bot)
    await notifier.notify_admin(
        "🛒 <b>شراء رقم جديد</b>\n\n"
        f"👤 المستخدم: {db_user.telegram_id} "
        f"(@{db_user.username or '-'})\n"
        f"{service.emoji} الخدمة: {service.name_ar}\n"
        f"🌍 الدولة: {country.flag} {country.name_ar}\n"
        f"📱 الرقم: {buy_result.phone_number}\n"
        f"🏭 المزود: {buy_result.provider.value}\n"
        f"💰 سعر البيع: {sell_price}$ | "
        f"التكلفة: {buy_result.cost_usd}$"
    )


# ══════════════ تحديث يدوي ══════════════


@router.callback_query(F.data.startswith("num_refresh:"))
async def refresh_order(callback: CallbackQuery, session, db_user: User, bot):
    order_id = int(callback.data.split(":")[1])
    order = await session.get(NumberOrder, order_id)

    if not order or order.user_id != db_user.id:
        await callback.answer(I18nService.t("number_request_missing", getattr(db_user, "language_code", "ar") or "ar"), show_alert=True)
        return

    if order.status != OrderStatus.PENDING:
        await callback.answer(I18nService.t("number_inactive_order", getattr(db_user, "language_code", "ar") or "ar"), show_alert=True)
        return

    await callback.answer(I18nService.t("number_refreshing", getattr(db_user, "language_code", "ar") or "ar"))

    from services.sms_receiver_service import SMSReceiverService
    from tasks.order_monitor import _expire_and_refund, _handle_code_received, _update_countdown

    notifier = NotificationService(bot)

    if order.expires_at and datetime.utcnow() > order.expires_at:
        await _expire_and_refund(session, order, notifier, bot)
        return

    try:
        status_result = await SMSReceiverService.check(order.provider, order.provider_order_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("فشل التحديث اليدوي للطلب %s: %s", order.id, exc)
        await callback.message.answer(I18nService.t("number_provider_check_failed", getattr(db_user, "language_code", "ar") or "ar"))
        return

    if status_result.status == "code_received" and status_result.sms_code:
        await _handle_code_received(session, order, status_result, notifier, bot)
        return

    if status_result.status in {"cancelled", "expired", "failed"}:
        await _expire_and_refund(session, order, notifier, bot)
        return

    await _update_countdown(bot, order)
    await callback.message.answer(I18nService.t("number_code_not_yet", getattr(db_user, "language_code", "ar") or "ar"))


# ══════════════ إلغاء الطلب ══════════════


@router.callback_query(F.data.startswith("num_cancel:"))
async def cancel_order_manual(callback: CallbackQuery, session, db_user: User):
    order_id = int(callback.data.split(":")[1])
    order = await session.get(NumberOrder, order_id)

    if not order or order.user_id != db_user.id:
        await callback.answer(I18nService.t("number_order_missing", getattr(db_user, "language_code", "ar") or "ar"), show_alert=True)
        return

    if order.status != OrderStatus.PENDING:
        await callback.answer(
            I18nService.t("number_cannot_cancel", getattr(db_user, "language_code", "ar") or "ar"),
            show_alert=True,
        )
        return

    try:
        await provider_manager.cancel_order(order.provider, order.provider_order_id)
    except Exception:
        pass

    order.status = OrderStatus.CANCELLED
    await session.commit()

    await BalanceService.add_balance(
        session,
        db_user.id,
        order.price_sell_usd,
        TransactionType.REFUND,
        description=f"استرجاع - إلغاء يدوي #{order.id}",
        related_table="number_orders",
        related_id=order.id,
        payment_reference=f"number_refund:{order.id}",
    )
    order.status = OrderStatus.REFUNDED
    await session.commit()

    try:
        await callback.message.edit_text(
            I18nService.t(
                "number_cancelled_refunded",
                getattr(db_user, "language_code", "ar") or "ar",
                amount=order.price_sell_usd,
            )
        )
    except TelegramBadRequest:
        pass
    await callback.answer(I18nService.t("number_cancel_done", getattr(db_user, "language_code", "ar") or "ar"))


# ══════════════ كود إضافي ══════════════


@router.callback_query(F.data.startswith("num_extra:"))
async def wait_extra_code(callback: CallbackQuery, session, db_user: User):
    order_id = int(callback.data.split(":")[1])
    order = await session.get(NumberOrder, order_id)

    if not order or order.user_id != db_user.id:
        await callback.answer(I18nService.t("number_order_missing", getattr(db_user, "language_code", "ar") or "ar"), show_alert=True)
        return

    order.awaiting_extra_code = True
    order.status = OrderStatus.PENDING
    order.expires_at = datetime.utcnow() + timedelta(minutes=2)
    await session.commit()

    await callback.message.answer(I18nService.t("number_extra_wait", getattr(db_user, "language_code", "ar") or "ar"))
    await callback.answer()


# ══════════════ إنهاء الطلب ══════════════


@router.callback_query(F.data.startswith("num_finish:"))
async def finish_order_manual(callback: CallbackQuery, session, db_user: User):
    order_id = int(callback.data.split(":")[1])
    order = await session.get(NumberOrder, order_id)

    if not order or order.user_id != db_user.id:
        await callback.answer(I18nService.t("number_order_missing", getattr(db_user, "language_code", "ar") or "ar"), show_alert=True)
        return

    order.awaiting_extra_code = False
    if order.status == OrderStatus.PENDING:
        order.status = OrderStatus.COMPLETED
    await session.commit()

    await callback.answer(I18nService.t("number_finish_done", getattr(db_user, "language_code", "ar") or "ar"))
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
