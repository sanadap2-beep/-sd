"""
هاندلر شراء واستعراض الأرقام:
- عرض الدول مرتبة تصاعدياً من الأرخص للأغلى.
- 25 دولة في كل صفحة بشكل مربعات (زراين) جنب بعض.
- أسماء الدول معربة دائماً مع العلم والسعر.
- الشراء الفردي والجملة مع استرجاع الرصيد التلقائي عند أي خطأ.
"""

import json
import logging
from datetime import datetime, timedelta
from decimal import ROUND_UP, Decimal

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func

from database.models import (
    Country,
    NumberOrder,
    NumberServer,
    NumberService,
    OrderStatus,
    ProviderName,
    TransactionType,
    User,
)
from providers.manager import provider_manager, ProviderUnavailableError
from providers.countries import (
    get_active_countries,
    get_active_number_services,
    get_number_service_by_code,
    resolve_country,
)
from services.number_server_service import (
    NumberServerService,
    public_server_label,
)
from services.pricing_service import PricingService
from services.currency_service import CurrencyService
from services.i18n_service import I18nService
from services.settings_service import SettingsService
from services.price_lock_service import PriceLockService
from services.agent_service import AgentService
from services.balance_service import BalanceService, InsufficientBalanceError
from services.bulk_number_service import BulkError, BulkNumberService
from services.feature_service import FeatureService
from services.notification_service import NotificationService
from keyboards.numbers import (
    bulk_confirm_kb,
    bulk_quantity_kb,
    countries_price_kb,
    confirm_purchase_kb,
    numbers_hub_kb,
    order_actions_kb,
    code_received_kb,
    after_number_order_kb,
    ready_number_packages_kb,
)
from keyboards.main_menu import insufficient_balance_kb, back_to_main_kb
from states.states import NumberBulkStates

logger = logging.getLogger(__name__)

router = Router(name="numbers")

DEFAULT_ORDER_TIMEOUT_MINUTES = 5


async def _get_order_timeout() -> int:
    return await SettingsService.get_int("order_timeout_minutes", DEFAULT_ORDER_TIMEOUT_MINUTES)


async def _check_rate_limit(session, user_id: int) -> bool:
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
    return result.scalar_one() == 0


async def _log_rate_limit(session, user_id: int):
    from database.models import RateLimitLog

    session.add(RateLimitLog(user_id=user_id, action="buy_number"))
    await session.commit()


async def _check_active_orders_limit(session, user_id: int) -> bool:
    max_orders = await SettingsService.get_int("max_active_orders", 3)
    result = await session.execute(
        select(func.count(NumberOrder.id)).where(
            NumberOrder.user_id == user_id,
            NumberOrder.status == OrderStatus.PENDING,
        )
    )
    return result.scalar_one() < max_orders


# ══════════════ قسم الأرقام الموحّد (واتساب + تيليجرام + أي قسم جديد) ══════════════


@router.callback_query(F.data == "num_hub")
async def numbers_hub(callback: CallbackQuery, session, db_user=None):
    """زر «📱 الأرقام» في المتجر: يفتح كل خدمات الأرقام المفعلة."""
    services = await get_active_number_services(session)
    if not services:
        await callback.message.edit_text(
            "📱 <b>الأرقام</b>\n\nلا توجد خدمات أرقام مفعلة حالياً.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔙 رجوع للمتجر", callback_data="store:home"
                        )
                    ]
                ]
            ),
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "📱 <b>الأرقام</b>\n\nاختر نوع الخدمة المطلوبة:",
        reply_markup=numbers_hub_kb(services, back_to_store=True),
    )
    await callback.answer()


# ══════════════ اختيار السيرفر/المزود (قبل الدول) ══════════════

SERVER_SELECTION_FEATURE = "number_server_selection"


def _servers_kb(service_code: str, servers: list[NumberServer]) -> InlineKeyboardMarkup:
    # ملاحظة: هذه الدالة متزامنة عمداً (لا تحتاج await) — كانت async سابقاً
    # فمرّرت كـ coroutine إلى reply_markup وتسبب ذلك في ValidationError.
    #
    # 🔒 الأسماء المعروضة محايدة ومرقّمة (سيرفر 1، سيرفر 2 ...) ولا تكشف
    # المزود إطلاقاً، والترقيم من ترتيب القائمة نفسها. 🟢 تعني أن الأدمن
    # علّم هذا السيرفر كـ«شغّال».
    b = InlineKeyboardBuilder()
    for index, server in enumerate(servers, start=1):
        b.button(
            text=public_server_label(index, bool(getattr(server, "is_working", False))),
            callback_data=f"num_server_pick:{service_code}:{server.id}",
            style="success",
        )
    b.button(text="🔙 رجوع", callback_data="num_hub")
    b.adjust(1)
    return b.as_markup()


SERVERS_HINT = (
    "🖥 <b>اختر السيرفر</b> أولاً:\n"
    "الأسعار والتوفر تختلف بين سيرفر وآخر.\n"
    "🟢 = السيرفر يعمل الآن."
)


@router.callback_query(F.data.startswith("num_server:"))
async def numbers_server_list(callback: CallbackQuery, session):
    parts = callback.data.split(":")
    if len(parts) < 2 or not parts[1]:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    service_code = parts[1]
    service = await get_number_service_by_code(session, service_code)
    if service is None or not service.is_active:
        await callback.answer("⚠️ الخدمة غير متاحة.", show_alert=True)
        return
    servers = await NumberServerService.list_servers(session, service.id, active_only=True)
    if not servers:
        await callback.answer("⚠️ لا توجد سيرفرات مفعلة لهذه الخدمة.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        f"{service.emoji} <b>أرقام {service.name_ar}</b>\n\n" + SERVERS_HINT,
        reply_markup=_servers_kb(service_code, servers),
    )


# ══════════════ اختيار الخدمة (عرض أول 10 دول مرتبة من الأرخص) ══════════════


@router.callback_query(F.data.startswith("num_svc:"))
async def number_service_selected(callback: CallbackQuery, session, db_user=None):
    service_code = callback.data.split(":")[1]
    service = await get_number_service_by_code(session, service_code)
    if service is None or not service.is_active:
        await callback.answer("⚠️ الخدمة غير متاحة حالياً.", show_alert=True)
        return

    if await FeatureService.enabled(SERVER_SELECTION_FEATURE, default=True):
        servers = await NumberServerService.ensure_defaults(session, service)
        active = [s for s in servers if s.is_active]
        if active:
            await callback.answer()
            await callback.message.edit_text(
                f"{service.emoji} <b>أرقام {service.name_ar}</b>\n\n" + SERVERS_HINT,
                reply_markup=_servers_kb(service_code, active),
            )
            return

    await callback.answer("⏳ جاري جلب أسعار الدول المتاحة...")

    try:
        from services.number_catalog_service import build_board
        entries = await build_board(session, service)
    except Exception as e:
        logger.error(f"فشل بناء لوحة أسعار الأرقام: {e}")
        entries = []

    if not entries:
        await callback.message.edit_text(
            f"{service.emoji} <b>أرقام {service.name_ar}</b>\n\n"
            "❌ لا توجد أرقام متوفرة حالياً لهذه الخدمة.\n"
            "يرجى المحاولة لاحقاً أو تجربة خدمة أخرى.",
            reply_markup=back_to_main_kb(),
        )
        return

    text = (
        f"{service.emoji} <b>أرقام {service.name_ar}</b>\n\n"
        "🟢 الدول مرتبة من <b>الأرخص إلى الأغلى</b>:\n"
        "اختر الدولة المطلوبة:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=countries_price_kb(service_code, entries, page=0),
    )


@router.callback_query(F.data.startswith("num_server_pick:"))
async def number_server_picked(callback: CallbackQuery, session, db_user=None):
    """المستخدم اختار سيرفر → نعرض دول هذا المزود فقط."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    service_code = parts[1]
    server_id = int(parts[2])
    service = await get_number_service_by_code(session, service_code)
    server = await NumberServerService.get(session, server_id) if service else None
    if service is None or server is None or not server.is_active:
        await callback.answer("⚠️ السيرفر غير متاح حالياً.", show_alert=True)
        return
    await callback.answer("⏳ جاري جلب أسعار الدول من هذا السيرفر...")

    try:
        from services.number_catalog_service import build_board
        entries = await build_board(session, service, server=server)
    except Exception as e:
        logger.error(f"فشل بناء لوحة أسعار سيرفر {server.id}: {e}")
        entries = []

    if not entries:
        server_title = await NumberServerService.public_label(session, server)
        await callback.message.edit_text(
            f"<b>{server_title}</b> · {service.emoji} {service.name_ar}\n\n"
            "❌ لا توجد أرقام متوفرة حالياً على هذا السيرفر.\n"
            "جرّب سيرفراً آخر أو عد لاحقاً.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🖥 تغيير السيرفر", callback_data=f"num_server:{service_code}", style="success")]
                ]
            ),
        )
        return

    server_title = await NumberServerService.public_label(session, server)
    text = (
        f"<b>{server_title}</b> · {service.emoji} {service.name_ar}\n\n"
        "🟢 الدول مرتبة من <b>الأرخص إلى الأغلى</b>:\n"
        "اختر الدولة المطلوبة:"
    )
    await callback.message.edit_text(
        text,
        reply_markup=countries_price_kb(
            service_code,
            entries,
            page=0,
            server_id=server.id,
            server_label=server_title,
        ),
    )


# ══════════════ التنقل بين صفحات الدول (10 دول بكل صفحة) ══════════════


@router.callback_query(F.data.startswith("num_page:"))
async def countries_page(callback: CallbackQuery, session, db_user=None):
    parts = callback.data.split(":")
    service_code = parts[1]
    page = int(parts[2])
    server_id = int(parts[3]) if len(parts) > 3 and parts[3] else None

    service = await get_number_service_by_code(session, service_code)
    if service is None:
        await callback.answer("⚠️ الخدمة غير موجودة.", show_alert=True)
        return

    server = None
    if server_id is not None:
        server = await NumberServerService.get(session, server_id)
        if server is None or not server.is_active:
            await callback.answer("⚠️ السيرفر غير متاح.", show_alert=True)
            return

    await callback.answer()

    try:
        from services.number_catalog_service import build_board
        entries = await build_board(session, service, server=server)
    except Exception:
        entries = []

    if not entries:
        await callback.message.edit_text(
            "❌ لا توجد أرقام متوفرة حالياً.",
            reply_markup=back_to_main_kb(),
        )
        return

    text = (
        f"{service.emoji} <b>أرقام {service.name_ar}</b>\n\n"
        "🟢 الدول مرتبة من <b>الأرخص إلى الأغلى</b>:\n"
        "اختر الدولة المطلوبة:"
    )
    server_title = None
    if server is not None:
        server_title = await NumberServerService.public_label(session, server)
        text = (
            f"<b>{server_title}</b> · {service.emoji} {service.name_ar}\n\n"
            "🟢 الدول مرتبة من <b>الأرخص إلى الأغلى</b>:\n"
            "اختر الدولة المطلوبة:"
        )
    await callback.message.edit_text(
        text,
        reply_markup=countries_price_kb(
            service_code,
            entries,
            page=page,
            server_id=server.id if server else None,
            server_label=server_title,
        ),
    )


# ══════════════ عرض تفاصيل السعر والشراء ══════════════


@router.callback_query(F.data.startswith("num_country:"))
async def show_price(callback: CallbackQuery, session, db_user=None):
    parts = callback.data.split(":")
    service_code = parts[1]
    country_ref = parts[2]
    server_id = int(parts[3]) if len(parts) > 3 and parts[3] else None

    service = await get_number_service_by_code(session, service_code)
    country = await resolve_country(session, country_ref)

    if not service or not country or not country.is_active:
        await callback.answer("⚠️ الدولة أو الخدمة غير متوفرة.", show_alert=True)
        return

    # الكود الداخلي للأسعار والطلبات (المرجع بالزر قد يكون رقماً)
    country_code = country.code

    server = None
    only_provider = None
    if server_id is not None:
        server = await NumberServerService.get(session, server_id)
        if server is None or not server.is_active:
            await callback.answer("⚠️ السيرفر غير متاح.", show_alert=True)
            return
        only_provider = ProviderName(server.provider) if NumberServerService.validate_provider(server.provider) else None

    await callback.answer("⏳ جاري تأكيد السعر والمخزون...")

    try:
        prices = await provider_manager.get_cheapest_price(service, country, session, only_provider=only_provider)
    except Exception as e:
        logger.error(f"خطأ جلب الأسعار: {e}")
        await callback.message.answer("⚠️ تعذّر الاتصال بالمزود، حاول بعد لحظات.")
        return

    if not prices:
        await callback.message.answer(
            f"❌ نفذت أرقام {country.flag} {country.name_ar} لخدمة {service.name_ar} حالياً.\n"
            "يرجى اختيار دولة أخرى."
        )
        return

    cheapest_provider = min(prices, key=prices.get)
    cost_usd = prices[cheapest_provider]

    if server is not None and server.margin_percent is not None:
        sell_price = (
            cost_usd * (Decimal("100") + server.margin_percent) / Decimal("100")
        ).quantize(Decimal("0.0001"), rounding=ROUND_UP)
    else:
        sell_price = await PricingService.calculate_sell_price(
            session, service_code, country_code, cheapest_provider, cost_usd
        )

    quote = await PriceLockService.create(
        service_code,
        country_code,
        cheapest_provider.value,
        cost_usd,
        sell_price,
    )

    from services.country_localization_service import display_flag, display_name

    price_display = await CurrencyService.format_dual(sell_price, db_user, session)
    server_line = ""
    if server is not None:
        server_line = (
            f"🖥 <b>السيرفر:</b> "
            f"{await NumberServerService.public_name(session, server)}\n"
        )
    await callback.message.edit_text(
        f"🌍 <b>الدولة:</b> {display_flag(country)} {display_name(country)}\n"
        f"{service.emoji} <b>الخدمة:</b> {service.name_ar}\n"
        f"{server_line}"
        f"💰 <b>السعر:</b> <b>{price_display}</b>\n\n"
        "🛡 <b>الضمان:</b> إذا لم يصل الكود خلال 5 دقائق يُسترجع رصيدك تلقائياً.\n\n"
        "هل تريد تأكيد شراء الرقم الآن؟",
        reply_markup=confirm_purchase_kb(
            service_code,
            country.id,
            quote.token,
            server_id=server_id,
        ),
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
        await callback.answer("الباقات الجاهزة غير مفعّلة حالياً.", show_alert=True)
        return
    if not await FeatureService.enabled("bulk_numbers"):
        await callback.message.edit_text("📦 الباقات الجاهزة تتطلب تفعيل ميزة الشراء بالجملة.")
        await callback.answer()
        return

    services = [svc for svc in await get_active_number_services(session) if svc.is_active]
    countries = [c for c in await get_active_countries(session) if c.is_active]
    if not services or not countries:
        await callback.message.edit_text("📦 لا توجد باقات أرقام مفعّلة حالياً.")
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
                    "country_id": country.id,
                    "quantity": quantity,
                }
            )
            if len(packages) >= max_cards:
                break
        if len(packages) >= max_cards:
            break

    await callback.message.edit_text(
        "📦 <b>باقات أرقام جاهزة</b>\n\nاختر باقة لعرض السعر والخصم قبل التنفيذ:",
        reply_markup=ready_number_packages_kb(packages),
    )
    await callback.answer()


# ══════════════ شراء الأرقام بالجملة ══════════════


async def _load_service_country(session, service_code: str, country_ref: str):
    service = await get_number_service_by_code(session, service_code)
    country = await resolve_country(session, country_ref)
    if not service or not country or not country.is_active:
        return None, None
    return service, country


async def _show_bulk_quote(callback_or_message, session, db_user: User, service_code: str, country_code: str, quantity: int, server_id: int | None = None):
    service, country = await _load_service_country(session, service_code, country_code)
    if service is None or country is None:
        await callback_or_message.answer("⚠️ الخدمة أو الدولة غير متاحة حالياً.")
        return

    server = None
    strict_provider = None
    if server_id is not None:
        server = await NumberServerService.get(session, server_id)
        if server is None or not server.is_active:
            await callback_or_message.answer("⚠️ السيرفر غير متاح.")
            return
        strict_provider = (
            ProviderName(server.provider)
            if NumberServerService.validate_provider(server.provider)
            else None
        )

    margin = server.margin_percent if server is not None else None
    try:
        if strict_provider is None:
            quote = await BulkNumberService.quote(session, service, country, quantity, margin_percent=margin)
        else:
            quote = await BulkNumberService.quote(
                session, service, country, quantity, strict_provider=strict_provider, margin_percent=margin
            )
    except BulkError as exc:
        await callback_or_message.answer(f"⚠️ {exc}")
        return

    from services.country_localization_service import display_flag, display_name

    unit_display = await CurrencyService.format_dual(quote["unit_price_usd"], db_user, session)
    total_display = await CurrencyService.format_dual(quote["total_usd"], db_user, session)
    discount_display = await CurrencyService.format_dual(quote["discount_usd"], db_user, session)

    agent_pct = await AgentService.active_percent(session, db_user.id)
    agent_line = ""
    final_total = quote["total_usd"]
    if agent_pct > 0:
        final_total = (
            quote["total_usd"] * (Decimal("100") - agent_pct) / Decimal("100")
        ).quantize(Decimal("0.0001"))
        final_total_display = await CurrencyService.format_dual(final_total, db_user, session)
        total_display = final_total_display
        agent_line = f"💼 خصم الوكيل: <b>{agent_pct}%</b>\n"

    server_line = ""
    if server is not None:
        server_line = (
            "🖥 السيرفر: "
            f"<b>{await NumberServerService.public_name(session, server)}</b>\n"
        )
    text = (
        "📦 <b>تأكيد شراء دفعة أرقام بالجملة</b>\n\n"
        f"{service.emoji} الخدمة: <b>{service.name_ar}</b>\n"
        f"{server_line}"
        f"🌍 الدولة: {display_flag(country)} <b>{display_name(country)}</b>\n"
        f"🔢 الكمية: <b>{quantity}</b>\n"
        f"💵 السعر الفردي: <b>{unit_display}</b>\n"
        f"🎁 خصم الجملة: <b>{quote['discount_percent']}%</b> (-{discount_display})\n"
        f"{agent_line}"
        f"💰 الإجمالي المطلوب: <b>{total_display}</b>\n\n"
        "🛡 إذا فشل أي رقم يتم استرجاع قيمته تلقائياً."
    )
    markup = bulk_confirm_kb(service_code, country.id, quantity, server_id=server_id)
    if isinstance(callback_or_message, CallbackQuery):
        await callback_or_message.message.edit_text(text, reply_markup=markup)
    else:
        await callback_or_message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("num_bulk_start:"))
async def bulk_start(callback: CallbackQuery, session, db_user: User):
    if not await FeatureService.enabled("bulk_numbers"):
        await callback.answer("📦 الشراء بالجملة غير مفعّل حالياً.", show_alert=True)
        return

    parts = callback.data.split(":")
    service_code = parts[1]
    country_code = parts[2]
    quote_token = parts[3] if len(parts) > 3 else ""
    server_id = int(parts[4]) if len(parts) > 4 and parts[4] else None
    service, country = await _load_service_country(session, service_code, country_code)
    if service is None or country is None:
        await callback.answer("⚠️ غير متاح.", show_alert=True)
        return

    max_qty = await BulkNumberService.max_quantity()
    await callback.answer()
    from services.country_localization_service import display_flag, display_name

    server_line = ""
    if server_id is not None:
        server = await NumberServerService.get(session, server_id)
        if server and server.is_active:
            server_line = (
                "🖥 السيرفر: "
                f"<b>{await NumberServerService.public_name(session, server)}</b>\n"
            )

    await callback.message.edit_text(
        f"📦 <b>شراء أرقام بالجملة</b>\n\n"
        f"{service.emoji} الخدمة: <b>{service.name_ar}</b>\n"
        f"{server_line}"
        f"🌍 الدولة: {display_flag(country)} <b>{display_name(country)}</b>\n"
        f"🔢 اختر الكمية أو اكتب كمية مخصصة (الحد الأقصى: <b>{max_qty}</b>):",
        reply_markup=bulk_quantity_kb(service_code, country.id, quote_token, server_id=server_id),
    )


@router.callback_query(F.data.startswith("num_bulk_qty:"))
async def bulk_quantity_selected(callback: CallbackQuery, session, db_user: User):
    parts = callback.data.split(":")
    quantity = int(parts[3])
    server_id = int(parts[4]) if len(parts) > 4 and parts[4] else None
    await callback.answer("⏳ جاري حساب سعر الدفعة...")
    await _show_bulk_quote(callback, session, db_user, parts[1], parts[2], quantity, server_id)


@router.callback_query(F.data.startswith("num_bulk_custom:"))
async def bulk_custom_quantity(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":")
    server_id = int(parts[4]) if len(parts) > 4 and parts[4] else None
    await state.set_state(NumberBulkStates.waiting_quantity)
    await state.update_data(service_code=parts[1], country_code=parts[2], server_id=server_id)
    await callback.message.answer("✍️ أرسل الكمية المطلوبة كرقم فقط (مثال: 25):")
    await callback.answer()


@router.message(NumberBulkStates.waiting_quantity)
async def bulk_custom_quantity_received(message: Message, state: FSMContext, session, db_user: User):
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ أرسل رقماً صحيحاً فقط، مثال: 25")
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
        data.get("server_id"),
    )


@router.callback_query(F.data.startswith("num_bulk_confirm:"))
async def bulk_confirm(callback: CallbackQuery, session, db_user: User, bot):
    if not await FeatureService.enabled("bulk_numbers"):
        await callback.answer("📦 الشراء بالجملة غير مفعّل.", show_alert=True)
        return

    parts = callback.data.split(":")
    service_code = parts[1]
    country_code = parts[2]
    quantity = int(parts[3])
    server_id = int(parts[4]) if len(parts) > 4 and parts[4] else None
    service, country = await _load_service_country(session, service_code, country_code)
    if service is None or country is None:
        await callback.answer("⚠️ غير متاح.", show_alert=True)
        return

    strict_provider = None
    server = None
    if server_id is not None:
        server = await NumberServerService.get(session, server_id)
        if server is None or not server.is_active:
            await callback.answer("⚠️ السيرفر غير متاح.", show_alert=True)
            return
        strict_provider = (
            ProviderName(server.provider)
            if NumberServerService.validate_provider(server.provider)
            else None
        )

    margin = server.margin_percent if server is not None else None
    try:
        if strict_provider is None:
            quote = await BulkNumberService.quote(session, service, country, quantity, margin_percent=margin)
        else:
            quote = await BulkNumberService.quote(
                session, service, country, quantity, strict_provider=strict_provider, margin_percent=margin
            )
    except BulkError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    # خصم الوكيل على إجمالي الدفعة (يُطبق قبل التحقق من الرصيد والخصم)
    agent_pct = await AgentService.active_percent(session, db_user.id)
    payable_total = quote["total_usd"]
    if agent_pct > 0:
        payable_total = (
            quote["total_usd"]
            * (Decimal("100") - agent_pct)
            / Decimal("100")
        ).quantize(Decimal("0.0001"))

    if db_user.balance < payable_total:
        notifier = NotificationService(bot)
        await notifier.notify_insufficient_balance(
            user_telegram_id=db_user.telegram_id,
            required_usd=str(payable_total),
            current_balance_usd=f"{db_user.balance:.2f}",
            reply_markup=insufficient_balance_kb(),
        )
        return

    await callback.answer("⏳ بدأ تنفيذ الدفعة...")
    await callback.message.edit_text(
        f"⏳ <b>جاري شراء الدفعة ({quantity} رقم)...</b>\nقد تستغرق العملية قليلاً."
    )

    try:
        result = await BulkNumberService.execute(
            session,
            db_user.id,
            service,
            country,
            quantity,
            timeout_minutes=await _get_order_timeout(),
            discount_percent=agent_pct if agent_pct > 0 else None,
            strict_provider=strict_provider,
            margin_percent=server.margin_percent if server is not None else None,
        )
    except BulkError as exc:
        await callback.message.answer(f"⚠️ {exc}")
        return
    except Exception as exc:
        logger.exception("فشل تنفيذ دفعة الأرقام: %s", exc)
        await callback.message.answer("❌ حدث خطأ غير متوقع أثناء تنفيذ الدفعة.")
        return

    text = (
        f"✅ <b>تم تنفيذ دفعة الأرقام</b>\n\n"
        f"🔢 المطلوب: <b>{result['requested']}</b>\n"
        f"✅ تم الشراء: <b>{result['succeeded']}</b>\n"
        f"❌ فشل: <b>{result['failed']}</b>\n"
        f"💰 الصافي المخصوم: <b>{result['net_charged_usd']}$</b>\n"
        f"↩️ المسترجع لرصيدك: <b>{result['refunded_usd']}$</b>\n\n"
        "سيتم إرسال الأكواد فور وصولها."
    )
    await callback.message.answer(text, reply_markup=after_number_order_kb())

    if result["orders"]:
        csv_data = BulkNumberService.export_csv(result["orders"])
        await bot.send_document(
            chat_id=db_user.telegram_id,
            document=BufferedInputFile(
                csv_data.encode("utf-8-sig"),
                filename=f"bulk_numbers_{service_code}_{country_code}.csv",
            ),
            caption="📄 ملف أرقام الدفعة بصيغة CSV",
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


# ══════════════ تأكيد الشراء الفردي واستلام الرقم ══════════════


@router.callback_query(F.data.startswith("num_confirm:"))
async def confirm_buy(
    callback: CallbackQuery,
    session,
    db_user: User,
    bot,
):
    parts = callback.data.split(":")
    service_code = parts[1]
    country_ref = parts[2]
    quote_token = parts[3] if len(parts) > 3 and parts[3] else None
    server_id = int(parts[4]) if len(parts) > 4 and parts[4] else None

    service = await get_number_service_by_code(session, service_code)
    country = await resolve_country(session, country_ref)

    if not service or not country or not country.is_active:
        await callback.answer("⚠️ الخدمة أو الدولة غير متاحة.", show_alert=True)
        return
    country_code = country.code

    server = None
    strict_provider = None
    server_provider = None
    if server_id is not None:
        server = await NumberServerService.get(session, server_id)
        if server is None or not server.is_active:
            await callback.answer("⚠️ السيرفر غير متاح.", show_alert=True)
            return
        server_provider = (
            ProviderName(server.provider)
            if NumberServerService.validate_provider(server.provider)
            else None
        )
        strict_provider = server_provider

    can_proceed = await _check_rate_limit(session, db_user.id)
    if not can_proceed:
        rate_seconds = await SettingsService.get_int("rate_limit_seconds", 30)
        await callback.answer(f"⏳ انتظر {rate_seconds} ثانية بين كل طلب.", show_alert=True)
        return

    can_order = await _check_active_orders_limit(session, db_user.id)
    if not can_order:
        max_orders = await SettingsService.get_int("max_active_orders", 3)
        await callback.answer(f"⚠️ لديك {max_orders} طلبات نشطة حالياً.", show_alert=True)
        return

    await callback.answer("⏳ جاري شراء الرقم...")

    try:
        if strict_provider is None:
            prices = await provider_manager.get_cheapest_price(service, country, session)
        else:
            prices = await provider_manager.get_cheapest_price(
                service, country, session, only_provider=strict_provider
            )
    except Exception:
        await callback.message.answer("⚠️ خطأ مؤقت بالاتصال بالمزود.")
        return

    if not prices:
        await callback.message.answer("❌ نفذت الأرقام لدى المزود.")
        return

    cheapest_provider = min(prices, key=prices.get)
    cost_usd = prices[cheapest_provider]
    if server is not None and server.margin_percent is not None:
        sell_price = (
            cost_usd * (Decimal("100") + server.margin_percent) / Decimal("100")
        ).quantize(Decimal("0.0001"), rounding=ROUND_UP)
    else:
        sell_price = await PricingService.calculate_sell_price(
            session, service_code, country_code, cheapest_provider, cost_usd
        )

    quote = await PriceLockService.get(quote_token, service_code, country_code)
    preferred_provider = None
    if quote is not None:
        sell_price = quote.sell_price_usd
        cost_usd = quote.cost_usd
        try:
            preferred_provider = ProviderName(quote.provider)
        except ValueError:
            preferred_provider = None

    # السيرفر المختار له أولوية مطلقة: حتى لو اختلف المزود في القفل القديم.
    if strict_provider is not None:
        preferred_provider = strict_provider

    # خصم الوكيل على سعر البيع (إن كان وكلاً فعّلاً)
    sell_price = await AgentService.apply_discount(session, db_user.id, sell_price)

    if db_user.balance < sell_price:
        notifier = NotificationService(bot)
        await notifier.notify_insufficient_balance(
            user_telegram_id=db_user.telegram_id,
            required_usd=str(sell_price),
            current_balance_usd=f"{db_user.balance:.2f}",
            reply_markup=insufficient_balance_kb(),
        )
        return

    try:
        await BalanceService.deduct_balance(
            session,
            db_user.id,
            sell_price,
            TransactionType.PURCHASE,
            description=f"شراء رقم {service.name_ar} - {country.name_ar}",
            is_purchase=True,
        )
    except InsufficientBalanceError:
        await callback.message.answer("⚠️ رصيدك غير كافٍ.")
        return

    try:
        if strict_provider is None:
            buy_result = await provider_manager.buy_number(
                service,
                country,
                session,
                preferred_provider=preferred_provider,
            )
        else:
            buy_result = await provider_manager.buy_number(
                service,
                country,
                session,
                preferred_provider=preferred_provider,
                strict_provider=strict_provider,
            )
    except ProviderUnavailableError as e:
        await BalanceService.add_balance(
            session,
            db_user.id,
            sell_price,
            TransactionType.REFUND,
            description="استرجاع - فشل شراء الرقم",
        )
        reason = (str(e) or "").strip()
        if len(reason) > 300:
            reason = reason[:300] + "…"
        text = "❌ تعذر سحب الرقم من المزود، تم استرجاع رصيدك بالكامل فوراً."
        if reason:
            text += f"\n\n📋 السبب: {reason}"
        await callback.message.answer(text)
        return
    except Exception:
        await BalanceService.add_balance(
            session,
            db_user.id,
            sell_price,
            TransactionType.REFUND,
            description="استرجاع - فشل شراء الرقم",
        )
        await callback.message.answer("❌ تعذر سحب الرقم من المزود، تم استرجاع رصيدك بالكامل فوراً.")
        return

    await PriceLockService.consume(quote_token)
    await _log_rate_limit(session, db_user.id)

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

    try:
        from services.weekly_challenge_service import WeeklyChallengeService

        await WeeklyChallengeService.record_event(
            session, db_user.id, amount=sell_price or Decimal("0"), event="orders"
        )
        await WeeklyChallengeService.record_event(
            session, db_user.id, amount=sell_price or Decimal("0"), event="spend_usd"
        )
    except Exception:
        logger.exception("فشل تحديث تقدم التحدي الأسبوعي")

    status_msg = await callback.message.answer(
        f"✅ <b>تم شراء الرقم بنجاح!</b>\n\n"
        f"📱 الرقم: <code>{buy_result.phone_number}</code>\n"
        f"⏳ بانتظار الكود... الوقت المتبقي: {timeout_minutes}:00\n\n"
        "سيتم تحديث هذه الرسالة تلقائياً عند وصول الكود.",
        reply_markup=order_actions_kb(order.id),
    )
    order.status_chat_id = status_msg.chat.id
    order.status_message_id = status_msg.message_id
    await session.commit()

    notifier = NotificationService(bot)
    await notifier.notify_admin(
        "🛒 <b>شراء رقم جديد</b>\n\n"
        f"👤 المستخدم: {db_user.telegram_id} (@{db_user.username or '-'})\n"
        f"{service.emoji} الخدمة: {service.name_ar}\n"
        f"🌍 الدولة: {country.flag} {country.name_ar}\n"
        f"📱 الرقم: <code>{buy_result.phone_number}</code>\n"
        f"💰 البيع: {sell_price}$ | التكلفة: {buy_result.cost_usd}$"
    )


# ══════════════ تحديث يدوي ══════════════


@router.callback_query(F.data.startswith("num_refresh:"))
async def refresh_order(callback: CallbackQuery, session, db_user: User, bot):
    order_id = int(callback.data.split(":")[1])
    order = await session.get(NumberOrder, order_id)

    if not order or order.user_id != db_user.id:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return

    if order.status != OrderStatus.PENDING:
        await callback.answer("ℹ️ هذا الطلب لم يعد نشطاً.", show_alert=True)
        return

    await callback.answer("🔄 جاري فحص الكود...")

    from services.sms_receiver_service import SMSReceiverService
    from tasks.order_monitor import _expire_and_refund, _handle_code_received, _update_countdown

    notifier = NotificationService(bot)

    if order.expires_at and datetime.utcnow() > order.expires_at:
        await _expire_and_refund(session, order, notifier, bot)
        return

    try:
        status_result = await SMSReceiverService.check(order.provider, order.provider_order_id)
    except Exception as exc:
        logger.warning(f"فحص الكود للطلب {order.id}: {exc}")
        await callback.message.answer("⚠️ تعذّر الاتصال بالمزود، سنواصل الفحص التلقائي.")
        return

    if status_result.status == "code_received" and status_result.sms_code:
        await _handle_code_received(session, order, status_result, notifier, bot)
        return

    if status_result.status in {"cancelled", "expired", "failed"}:
        await _expire_and_refund(session, order, notifier, bot)
        return

    await _update_countdown(bot, order)
    await callback.message.answer("⏳ لم يصل الكود بعد، سنواصل التحديث تلقائياً.")


# ══════════════ إلغاء الطلب ══════════════


@router.callback_query(F.data.startswith("num_cancel:"))
async def cancel_order_manual(callback: CallbackQuery, session, db_user: User):
    order_id = int(callback.data.split(":")[1])
    order = await session.get(NumberOrder, order_id)

    if not order or order.user_id != db_user.id:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return

    if order.status != OrderStatus.PENDING:
        await callback.answer("⚠️ لا يمكن إلغاء هذا الطلب.", show_alert=True)
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
    )
    order.status = OrderStatus.REFUNDED
    await session.commit()

    try:
        await callback.message.edit_text(
            f"❌ تم إلغاء الطلب واسترجاع <b>{order.price_sell_usd}$</b> إلى رصيدك.",
            reply_markup=after_number_order_kb(),
        )
    except TelegramBadRequest:
        pass
    await callback.answer("✅ تم الإلغاء والاسترجاع.")


# ══════════════ كود إضافي ══════════════


@router.callback_query(F.data.startswith("num_extra:"))
async def wait_extra_code(callback: CallbackQuery, session, db_user: User):
    order_id = int(callback.data.split(":")[1])
    order = await session.get(NumberOrder, order_id)

    if not order or order.user_id != db_user.id:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return

    order.awaiting_extra_code = True
    order.status = OrderStatus.PENDING
    order.expires_at = datetime.utcnow() + timedelta(minutes=2)
    await session.commit()

    await callback.message.answer("🔄 تم تمديد الانتظار لدقيقتين لاستقبال كود إضافي.")
    await callback.answer()


# ══════════════ إنهاء الطلب ══════════════


@router.callback_query(F.data.startswith("num_finish:"))
async def finish_order_manual(callback: CallbackQuery, session, db_user: User):
    order_id = int(callback.data.split(":")[1])
    order = await session.get(NumberOrder, order_id)

    if not order or order.user_id != db_user.id:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return

    order.awaiting_extra_code = False
    if order.status == OrderStatus.PENDING:
        order.status = OrderStatus.COMPLETED
    await session.commit()

    await callback.answer("✅ تم إنهاء الطلب بنجاح.")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.message.answer(
        "يمكنك شراء رقم آخر أو العودة للقائمة الرئيسية:",
        reply_markup=after_number_order_kb(),
    )