"""
هاندلر طرق الدفع الست:
1) شام كاش يدوي
2) نجوم تليجرام (في handlers/deposit.py)
3) شام كاش تلقائي (Sam API)
4) USDT تلقائي (Plisio)
5) USDT يدوي
6) طرق دفع أخرى
"""

import html
import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from config import settings
from database.models import (
    DepositRequest,
    DepositStatus,
    User,
    TransactionType,
    AutoInvoice,
    AutoInvoiceMethod,
    AutoInvoiceStatus,
)
from services.input_validation_service import InputValidationError, InputValidationService
from services.settings_service import SettingsService
from services.balance_service import BalanceService
from services.notification_service import NotificationService
from services.sam_api_service import (
    sam_api_client,
    SamApiError,
    SamApiExpiredError,
)
from services.plisio_service import (
    plisio_client,
    PlisioAPIError,
    PlisioConnectionError,
    PlisioError,
)
from services.payment_method_service import PAYMENT_METHOD_SETTINGS, payment_method_enabled
from states.states import (
    ShamCashManualStates,
    UsdtManualStates,
    ShamCashAutoStates,
    UsdtAutoStates,
)
from keyboards.admin import deposit_decision_kb
from keyboards.deposit_methods import (
    deposit_methods_kb,
    start_deposit_kb,
    usdt_networks_kb,
    shamcash_currency_kb,
    shamcash_invoice_kb,
    amount_presets_kb,
    usdt_auto_invoice_kb,
    cancel_deposit_kb,
)
from keyboards.main_menu import back_to_main_kb

_AUTO_USDT_NETWORK_CURRENCIES = {
    "TRC20": "USDT_TRX",
    "BEP20": "USDT_BSC",
}

logger = logging.getLogger(__name__)

router = Router(name="deposit_methods")


def _parse_positive_amount(value: str | None) -> Decimal:
    """Parse a finite positive monetary amount from a Telegram message."""
    if not value:
        raise InvalidOperation
    try:
        return InputValidationService.positive_money(value)
    except InputValidationError:
        raise InvalidOperation from None


def _safe_plisio_error(error: PlisioError) -> str:
    """Return a short HTML-safe provider error without leaking credentials."""
    if isinstance(error, PlisioAPIError):
        prefix = "رفض مزود الدفع الطلب"
    elif isinstance(error, PlisioConnectionError):
        prefix = "تعذر الاتصال بمزود الدفع"
    else:
        prefix = "تعذر إنشاء فاتورة الدفع"
    return html.escape(f"{prefix}: {str(error)[:400]}")


def _parse_invoice_expiration(value, fallback_minutes: int = 30) -> datetime:
    """Parse Plisio's Unix/ISO expiry value, with a safe local fallback."""
    fallback = datetime.utcnow() + timedelta(minutes=fallback_minutes)
    if value is None or value == "":
        return fallback

    try:
        if isinstance(value, (int, float)):
            timestamp = float(value)
            if timestamp > 100_000_000_000:
                timestamp /= 1000
            return datetime.utcfromtimestamp(timestamp)

        text = str(value).strip()
        if text.isdigit():
            timestamp = float(text)
            if timestamp > 100_000_000_000:
                timestamp /= 1000
            return datetime.utcfromtimestamp(timestamp)

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError, OverflowError):
        return fallback


async def _get_owned_invoice(session, invoice_id: int, user_id: int):
    """Return an invoice only when it belongs to the current user.

    Invoice IDs are embedded in callback data, so ownership must never be
    inferred from the button message alone.
    """
    invoice = await session.get(AutoInvoice, invoice_id)
    if invoice is None or invoice.user_id != user_id:
        return None
    return invoice


async def _proof_already_submitted(session, proof: str) -> bool:
    result = await session.execute(
        select(DepositRequest.id)
        .where(
            DepositRequest.proof_tx_number == proof,
            DepositRequest.status.in_([DepositStatus.PENDING, DepositStatus.APPROVED]),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


# Kept as a compatibility alias for code and tests that import this mapping
# from the handler. The actual availability policy lives in the shared service.
_PAYMENT_METHOD_SETTINGS = PAYMENT_METHOD_SETTINGS


async def _payment_method_enabled(method: str) -> bool:
    """Compatibility wrapper used by the user deposit handlers."""
    return await payment_method_enabled(method)


async def _require_payment_method(target, method: str, state: FSMContext | None = None) -> bool:
    """Fail closed even when a user replays a hidden/old payment callback."""
    if await _payment_method_enabled(method):
        return True
    if state is not None:
        await state.clear()
    if isinstance(target, CallbackQuery):
        await target.answer("⚠️ طريقة الدفع موقوفة حالياً.", show_alert=True)
    else:
        await target.answer("⚠️ طريقة الدفع موقوفة حالياً.")
    return False


# ══════════════ قائمة طرق الدفع الست ══════════════


async def _show_deposit_menu(target, state: FSMContext):
    """يعرض قائمة طرق الدفع الست."""
    await state.clear()

    shamcash_manual = await _payment_method_enabled("shamcash_manual")
    stars = await _payment_method_enabled("stars")
    usdt_manual = await _payment_method_enabled("usdt_manual")
    shamcash_auto = await _payment_method_enabled("shamcash_auto")
    usdt_auto = await _payment_method_enabled("usdt_auto")
    other = await _payment_method_enabled("other")

    text = "💰 <b>شحن الرصيد</b>\n\nاختر طريقة الشحن المناسبة لك:"
    kb = deposit_methods_kb(
        shamcash_manual_enabled=shamcash_manual,
        stars_enabled=stars,
        usdt_manual_enabled=usdt_manual,
        shamcash_auto_enabled=shamcash_auto,
        usdt_auto_enabled=usdt_auto,
        other_enabled=other,
    )

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
        except Exception:
            await target.message.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


# ══════════════ 6) طرق دفع أخرى ══════════════


@router.callback_query(F.data == "deposit:other")
async def deposit_other(callback: CallbackQuery, state: FSMContext):
    if not await _require_payment_method(callback, "other", state):
        return
    await callback.answer()
    await state.clear()
    support = await SettingsService.get("support_username", "@support")
    description = await SettingsService.get(
        "payment_other_description", "📞 <b>طرق دفع أخرى</b>\n\nتواصل مع الدعم:\n{support_username}"
    )
    description = description.replace("{support_username}", support)
    await callback.message.edit_text(
        description,
        reply_markup=back_to_main_kb(),
    )


# ══════════════════════════════════════════
# ══════════════ 1) شام كاش يدوي ══════════════
# ══════════════════════════════════════════


@router.callback_query(F.data == "deposit:shamcash_manual")
async def shamcash_manual_start(callback: CallbackQuery, state: FSMContext):
    if not await _require_payment_method(callback, "shamcash_manual", state):
        return
    await callback.answer()
    await state.clear()
    description = await SettingsService.get(
        "payment_shamcash_manual_description", "💵 الشحن اليدوي عبر شام كاش"
    )
    await callback.message.edit_text(
        description,
        reply_markup=start_deposit_kb("shamcash_manual"),
    )


@router.callback_query(F.data == "deposit_start:shamcash_manual")
async def shamcash_manual_amount_ask(callback: CallbackQuery, state: FSMContext):
    if not await _require_payment_method(callback, "shamcash_manual", state):
        return
    await callback.answer()
    min_deposit = await SettingsService.get_decimal("min_deposit_shamcash_usd", Decimal("0.5"))
    await callback.message.edit_text(
        f"💵 <b>شام كاش يدوي</b>\n\n"
        f"الحد الأدنى للشحن: <b>{min_deposit}$</b>\n\n"
        "اختر مبلغاً جاهزاً أو أرسل مبلغاً مخصصاً بالدولار:",
        reply_markup=amount_presets_kb("shamcash_manual", "USD", "deposit:shamcash_manual"),
    )
    await state.set_state(ShamCashManualStates.waiting_amount)


async def _start_shamcash_manual_proof(target, state: FSMContext, amount: Decimal) -> bool:
    if not await _require_payment_method(target, "shamcash_manual", state):
        return False
    min_deposit = await SettingsService.get_decimal("min_deposit_shamcash_usd", Decimal("0.5"))
    if amount < min_deposit:
        await target.answer(f"⚠️ الحد الأدنى هو {min_deposit}$")
        return False

    await state.update_data(amount_usd=str(amount))

    address = await SettingsService.get("shamcash_manual_address", "")
    address = address or settings.SHAMCASH_MANUAL_ADDRESS
    name = await SettingsService.get("shamcash_manual_name", "")
    rate = await SettingsService.get_decimal("usd_to_syp_rate", Decimal("130"))
    amount_syp = (amount * rate).quantize(Decimal("1"))

    text = (
        f"💵 <b>تعليمات التحويل - شام كاش</b>\n\n"
        f"💰 المبلغ المطلوب: <b>{amount}$</b>\n"
        f"💰 يعادل: <b>{amount_syp:,} ل.س</b>\n\n"
        f"📬 عنوان المحفظة:\n"
        f"<code>{address}</code>\n"
    )
    if name:
        text += f"👤 الاسم: <b>{name}</b>\n"
    text += (
        "\n📱 <b>خطوات التحويل:</b>\n"
        "1) افتح تطبيق شام كاش\n"
        "2) اضغط على تحويل\n"
        "3) الصق عنوان المحفظة أعلاه\n"
        "4) أدخل المبلغ\n"
        "5) نفذ التحويل\n\n"
        "📸 <b>الآن أرسل صورة إثبات التحويل:</b>"
    )
    await target.answer(text, reply_markup=cancel_deposit_kb())
    await state.set_state(ShamCashManualStates.waiting_proof_photo)
    return True


@router.message(ShamCashManualStates.waiting_amount)
async def shamcash_manual_amount_received(message: Message, state: FSMContext):
    try:
        amount = _parse_positive_amount(message.text)
    except (InvalidOperation, AttributeError):
        await message.answer("⚠️ أرسل رقماً صحيحاً، مثال: 5")
        return
    await _start_shamcash_manual_proof(message, state, amount)


@router.callback_query(F.data.startswith("dep_custom:"))
async def deposit_custom_amount(callback: CallbackQuery, state: FSMContext):
    _, method, currency = callback.data.split(":", 2)
    if not await _require_payment_method(callback, method, state):
        return
    if method == "shamcash_manual":
        await state.set_state(ShamCashManualStates.waiting_amount)
        await callback.message.answer("✍️ أرسل المبلغ بالدولار، مثال: 5")
    elif method == "shamcash_auto":
        await state.update_data(currency=currency)
        await state.set_state(ShamCashAutoStates.waiting_amount)
        unit = "بالدولار" if currency == "USD" else "بالليرة السورية"
        await callback.message.answer(f"✍️ أرسل المبلغ {unit}:")
    else:
        await callback.answer("طريقة غير مدعومة.", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data.startswith("dep_amount:"))
async def deposit_preset_amount(
    callback: CallbackQuery,
    state: FSMContext,
    session,
    db_user: User,
    bot,
):
    _, method, currency, raw_amount = callback.data.split(":", 3)
    if not await _require_payment_method(callback, method, state):
        return
    try:
        amount = Decimal(raw_amount)
    except InvalidOperation:
        await callback.answer("مبلغ غير صالح.", show_alert=True)
        return

    await callback.answer("⏳ جاري تجهيز الشحن...")
    if method == "shamcash_manual":
        await _start_shamcash_manual_proof(callback.message, state, amount)
        return
    if method == "shamcash_auto":
        await _create_shamcash_auto_invoice(
            callback.message, state, session, db_user, bot, amount, currency
        )
        return
    await callback.message.answer("⚠️ طريقة الشحن غير مدعومة لهذا الاختصار.")


@router.message(ShamCashManualStates.waiting_proof_photo, F.photo)
async def shamcash_manual_photo_received(message: Message, state: FSMContext):
    if not await _require_payment_method(message, "shamcash_manual", state):
        return
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await message.answer("🔢 الآن أرسل <b>رقم عملية التحويل</b>:")
    await state.set_state(ShamCashManualStates.waiting_tx_number)


@router.message(ShamCashManualStates.waiting_proof_photo)
async def shamcash_manual_photo_invalid(message: Message):
    await message.answer("⚠️ الرجاء إرسال صورة إثبات التحويل (وليس نصاً).")


@router.message(ShamCashManualStates.waiting_tx_number)
async def shamcash_manual_tx_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
    bot,
):
    if not await _require_payment_method(message, "shamcash_manual", state):
        return
    data = await state.get_data()
    amount_usd = Decimal(data["amount_usd"])
    photo_file_id = data["photo_file_id"]
    tx_number = message.text.strip()

    if await _proof_already_submitted(session, tx_number):
        await message.answer("⚠️ رقم العملية مستخدم مسبقاً أو قيد المراجعة.")
        await state.clear()
        return

    deposit = DepositRequest(
        user_id=db_user.id,
        amount_usd=amount_usd,
        proof_photo_file_id=photo_file_id,
        proof_tx_number=tx_number,
        payment_method="ShamCash Manual",
        status=DepositStatus.PENDING,
    )
    session.add(deposit)
    await session.commit()
    await session.refresh(deposit)

    notifier = NotificationService(bot)
    sent_msg_id = await notifier.notify_new_deposit(
        user_telegram_id=db_user.telegram_id,
        username=db_user.username,
        amount_usd=str(amount_usd),
        tx_number=tx_number,
        deposit_id=deposit.id,
        photo_file_id=photo_file_id,
        reply_markup=deposit_decision_kb(deposit.id),
    )

    if sent_msg_id:
        deposit.admin_chat_message_id = sent_msg_id
        await session.commit()

    await message.answer(
        "✅ تم إرسال طلب الشحن بنجاح!\n\n"
        f"💰 المبلغ: <b>{amount_usd}$</b>\n"
        f"🔢 رقم العملية: <code>{tx_number}</code>\n\n"
        "⏳ بانتظار موافقة الإدارة.\n"
        "سيصلك إشعار فور مراجعة طلبك.",
        reply_markup=back_to_main_kb(),
    )
    await state.clear()


# ══════════════════════════════════════════
# ══════════════ 5) USDT يدوي ══════════════
# ══════════════════════════════════════════


@router.callback_query(F.data == "deposit:usdt_manual")
async def usdt_manual_start(callback: CallbackQuery, state: FSMContext):
    if not await _require_payment_method(callback, "usdt_manual", state):
        return
    await callback.answer()
    await state.clear()
    description = await SettingsService.get(
        "payment_usdt_manual_description", "₮ الشحن اليدوي عبر USDT"
    )
    await callback.message.edit_text(
        description,
        reply_markup=start_deposit_kb("usdt_manual"),
    )


@router.callback_query(F.data == "deposit_start:usdt_manual")
async def usdt_manual_network_ask(callback: CallbackQuery, state: FSMContext):
    if not await _require_payment_method(callback, "usdt_manual", state):
        return
    await callback.answer()
    await callback.message.edit_text(
        "₮ <b>USDT يدوي</b>\n\n"
        "اختر الشبكة التي ستحوّل عبرها:\n\n"
        "🟢 <b>TRC20</b> - الأرخص (رسوم منخفضة)\n"
        "🔵 <b>ERC20</b> - رسوم مرتفعة\n"
        "🟡 <b>BEP20</b> - رسوم متوسطة",
        reply_markup=usdt_networks_kb("manual"),
    )
    await state.set_state(UsdtManualStates.waiting_network)


@router.callback_query(F.data.startswith("usdt_net:manual:"))
async def usdt_manual_network_selected(callback: CallbackQuery, state: FSMContext):
    if not await _require_payment_method(callback, "usdt_manual", state):
        return
    network = callback.data.split(":")[2]
    await state.update_data(network=network)
    await callback.answer()

    min_deposit = await SettingsService.get_decimal("min_deposit_usdt_usd", Decimal("2"))
    await callback.message.edit_text(
        f"₮ <b>USDT يدوي - {network}</b>\n\n"
        f"الحد الأدنى للشحن: <b>{min_deposit}$</b>\n\n"
        "أرسل المبلغ بالدولار (مثال: 10):",
        reply_markup=cancel_deposit_kb(),
    )
    await state.set_state(UsdtManualStates.waiting_amount)


@router.message(UsdtManualStates.waiting_amount)
async def usdt_manual_amount_received(message: Message, state: FSMContext):
    if not await _require_payment_method(message, "usdt_manual", state):
        return
    try:
        amount = _parse_positive_amount(message.text)
    except (InvalidOperation, AttributeError):
        await message.answer("⚠️ أرسل رقماً صحيحاً.")
        return

    min_deposit = await SettingsService.get_decimal("min_deposit_usdt_usd", Decimal("2"))
    if amount < min_deposit:
        await message.answer(f"⚠️ الحد الأدنى هو {min_deposit}$")
        return

    data = await state.get_data()
    network = data["network"]
    await state.update_data(amount_usd=str(amount))

    address_key = f"usdt_{network.lower()}_address"
    address = await SettingsService.get(address_key, "")
    address = address or getattr(settings, address_key.upper(), "")

    if not address:
        await message.answer(f"⚠️ عنوان محفظة {network} غير محدد. تواصل مع الدعم.")
        await state.clear()
        return

    text = (
        f"₮ <b>USDT {network} - تعليمات التحويل</b>\n\n"
        f"💰 المبلغ المطلوب: <b>{amount} USDT</b>\n\n"
        f"📬 عنوان المحفظة:\n"
        f"<code>{address}</code>\n\n"
        f"⚠️ <b>تنبيهات مهمة:</b>\n"
        f"• تأكد أنك تحول عبر شبكة <b>{network}</b>\n"
        f"• الأموال المرسلة عبر شبكة خاطئة تُفقد نهائياً\n"
        f"• أرسل المبلغ المحدد بالضبط\n\n"
        "📸 <b>الآن أرسل صورة إثبات التحويل:</b>"
    )
    await message.answer(text, reply_markup=cancel_deposit_kb())
    await state.set_state(UsdtManualStates.waiting_proof_photo)


@router.message(UsdtManualStates.waiting_proof_photo, F.photo)
async def usdt_manual_photo_received(message: Message, state: FSMContext):
    if not await _require_payment_method(message, "usdt_manual", state):
        return
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await message.answer("🔢 الآن أرسل <b>TX Hash</b> (رقم العملية على البلوكشين):")
    await state.set_state(UsdtManualStates.waiting_tx_hash)


@router.message(UsdtManualStates.waiting_proof_photo)
async def usdt_manual_photo_invalid(message: Message):
    await message.answer("⚠️ الرجاء إرسال صورة إثبات التحويل.")


@router.message(UsdtManualStates.waiting_tx_hash)
async def usdt_manual_tx_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
    bot,
):
    if not await _require_payment_method(message, "usdt_manual", state):
        return
    data = await state.get_data()
    amount_usd = Decimal(data["amount_usd"])
    photo_file_id = data["photo_file_id"]
    network = data["network"]
    tx_hash = message.text.strip()

    if await _proof_already_submitted(session, tx_hash):
        await message.answer("⚠️ TX Hash مستخدم مسبقاً أو قيد المراجعة.")
        await state.clear()
        return

    deposit = DepositRequest(
        user_id=db_user.id,
        amount_usd=amount_usd,
        proof_photo_file_id=photo_file_id,
        proof_tx_number=tx_hash,
        payment_method=f"USDT {network} Manual",
        status=DepositStatus.PENDING,
    )
    session.add(deposit)
    await session.commit()
    await session.refresh(deposit)

    notifier = NotificationService(bot)
    caption = (
        "🆕 <b>طلب شحن USDT جديد</b>\n\n"
        f"👤 المستخدم: {db_user.telegram_id} "
        f"(@{db_user.username or '-'})\n"
        f"💵 المبلغ: <b>{amount_usd} USDT</b>\n"
        f"🌐 الشبكة: <b>{network}</b>\n"
        f"🔢 TX Hash: <code>{tx_hash}</code>\n"
        f"🆔 رقم الطلب: #{deposit.id}\n"
        f"⏰ الوقت: "
        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
    )
    sent_msg_id = await notifier.notify_admin_photo(
        photo_file_id=photo_file_id,
        caption=caption,
        reply_markup=deposit_decision_kb(deposit.id),
    )

    if sent_msg_id:
        deposit.admin_chat_message_id = sent_msg_id
        await session.commit()

    await message.answer(
        "✅ تم إرسال طلب الشحن بنجاح!\n\n"
        f"💰 المبلغ: <b>{amount_usd} USDT</b>\n"
        f"🌐 الشبكة: <b>{network}</b>\n"
        f"🔢 TX Hash: <code>{tx_hash}</code>\n\n"
        "⏳ بانتظار موافقة الإدارة.\n"
        "سيصلك إشعار فور مراجعة طلبك.",
        reply_markup=back_to_main_kb(),
    )
    await state.clear()


# ══════════════════════════════════════════════
# ══════════════ 3) شام كاش تلقائي ══════════════
# ══════════════════════════════════════════════


@router.callback_query(F.data == "deposit:shamcash_auto")
async def shamcash_auto_start(callback: CallbackQuery, state: FSMContext):
    if not await _require_payment_method(callback, "shamcash_auto", state):
        return
    await callback.answer()
    await state.clear()
    description = await SettingsService.get(
        "payment_shamcash_auto_description", "💳 الشحن الفوري عبر شام كاش"
    )
    await callback.message.edit_text(
        description,
        reply_markup=start_deposit_kb("shamcash_auto"),
    )


@router.callback_query(F.data == "deposit_start:shamcash_auto")
async def shamcash_auto_currency_ask(callback: CallbackQuery, state: FSMContext):
    if not await _require_payment_method(callback, "shamcash_auto", state):
        return
    await callback.answer()
    await callback.message.edit_text(
        "💳 <b>شام كاش تلقائي</b>\n\nاختر العملة التي ستدفع بها:",
        reply_markup=shamcash_currency_kb(),
    )
    await state.set_state(ShamCashAutoStates.waiting_currency)


@router.callback_query(F.data.startswith("shamcash_curr:"))
async def shamcash_auto_currency_selected(callback: CallbackQuery, state: FSMContext):
    if not await _require_payment_method(callback, "shamcash_auto", state):
        return
    currency = callback.data.split(":")[1]
    await state.update_data(currency=currency)
    await callback.answer()

    min_deposit_usd = await SettingsService.get_decimal("min_deposit_shamcash_usd", Decimal("0.5"))

    preset_amounts = None
    if currency == "USD":
        text = (
            f"💳 <b>شام كاش تلقائي - USD</b>\n\n"
            f"الحد الأدنى: <b>{min_deposit_usd}$</b>\n\n"
            "أرسل المبلغ بالدولار (مثال: 5):"
        )
    else:
        rate = await SettingsService.get_decimal("usd_to_syp_rate", Decimal("130"))
        min_deposit_syp = (min_deposit_usd * rate).quantize(Decimal("1"))
        preset_amounts = [
            int((Decimal(str(usd)) * rate).quantize(Decimal("1")))
            for usd in (1, 3, 5, 10, 25)
        ]
        text = (
            f"💳 <b>شام كاش تلقائي - SYP</b>\n\n"
            f"سعر الصرف اليومي: <b>1$ = {rate:,} ل.س</b>\n"
            f"الحد الأدنى: <b>{min_deposit_syp:,} ل.س</b>\n\n"
            "أرسل المبلغ بالليرة السورية أو اختر مبلغاً جاهزاً:"
        )

    await callback.message.edit_text(
        text + "\n\nاختر مبلغاً جاهزاً أو أرسل مبلغاً مخصصاً:",
        reply_markup=amount_presets_kb(
            "shamcash_auto",
            currency,
            "deposit_start:shamcash_auto",
            amounts=preset_amounts,
        ),
    )
    await state.set_state(ShamCashAutoStates.waiting_amount)


async def _create_shamcash_auto_invoice(
    target,
    state: FSMContext,
    session,
    db_user: User,
    bot,
    amount: Decimal,
    currency: str,
):
    if not await _require_payment_method(target, "shamcash_auto", state):
        return
    min_deposit_usd = await SettingsService.get_decimal("min_deposit_shamcash_usd", Decimal("0.5"))
    rate = await SettingsService.get_decimal("usd_to_syp_rate", Decimal("130"))

    if currency == "USD":
        if amount < min_deposit_usd:
            await target.answer(f"⚠️ الحد الأدنى هو {min_deposit_usd}$")
            return
        amount_usd = amount
    else:
        min_deposit_syp = min_deposit_usd * rate
        if amount < min_deposit_syp:
            await target.answer(f"⚠️ الحد الأدنى هو {min_deposit_syp:,} ل.س")
            return
        amount_usd = (amount / rate).quantize(Decimal("0.0001"))

    await target.answer("⏳ جاري إنشاء الفاتورة...")

    try:
        invoice_data = await sam_api_client.create_invoice(
            amount=amount,
            currency=currency,
        )
    except SamApiError as e:
        logger.error(f"فشل إنشاء فاتورة Sam API: {e}")
        await target.answer(
            f"❌ فشل إنشاء الفاتورة:\n<code>{e}</code>\n\n"
            "حاول مجدداً لاحقاً أو استخدم طريقة دفع أخرى.",
            reply_markup=back_to_main_kb(),
        )
        await state.clear()
        return

    external_id = invoice_data.get("invoiceId")
    payment_url = invoice_data.get("paymentUrl")
    expires_at_str = invoice_data.get("expiresAt")

    if not external_id:
        await target.answer(
            "❌ خطأ في استجابة الخادم.",
            reply_markup=back_to_main_kb(),
        )
        await state.clear()
        return

    try:
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00")).replace(
            tzinfo=None
        )
    except Exception:
        expires_at = datetime.utcnow() + timedelta(minutes=15)

    address = await SettingsService.get("shamcash_manual_address", "")
    address = (
        invoice_data.get("paymentAddress")
        or invoice_data.get("payment_address")
        or address
        or settings.SAM_API_WALLET_ADDRESS
    )
    address = str(address or "").strip() or None

    invoice = AutoInvoice(
        user_id=db_user.id,
        method=AutoInvoiceMethod.SHAMCASH_AUTO,
        external_invoice_id=external_id,
        amount_usd=amount_usd,
        amount_original=amount,
        currency=currency,
        payment_address=address,
        payment_url=payment_url,
        status=AutoInvoiceStatus.PENDING,
        expires_at=expires_at,
        raw_data=json.dumps(invoice_data, ensure_ascii=False),
    )
    session.add(invoice)
    await session.commit()
    await session.refresh(invoice)

    remaining = expires_at - datetime.utcnow()
    minutes = int(remaining.total_seconds() // 60)
    seconds = int(remaining.total_seconds() % 60)

    destination_text = (
        f"📬 عنوان الاستلام:\n<code>{html.escape(address)}</code>"
        if address
        else "📬 افتح صفحة الدفع من الزر أدناه لمعرفة تفاصيل التحويل."
    )
    text = (
        "💳 <b>فاتورة شام كاش</b>\n\n"
        f"⏱ الوقت المتبقي: <b>{minutes}:{seconds:02d}</b>\n"
        f"💰 المبلغ المطلوب: <b>{amount} {currency}</b>\n"
        f"💵 يعادل: <b>{amount_usd}$</b>\n\n"
        f"{destination_text}\n\n"
        f"📱 <b>خطوات الدفع:</b>\n"
        "1) افتح تطبيق شام كاش\n"
        "2) حوّل المبلغ للعنوان الظاهر أو داخل صفحة الدفع\n"
        "3) انسخ رقم العملية من التطبيق\n"
        "4) أرسل رقم العملية هنا للتحقق\n\n"
        "🔢 <b>أرسل رقم العملية الآن:</b>"
    )

    status_msg = await target.answer(
        text,
        reply_markup=shamcash_invoice_kb(invoice.id, payment_url),
    )
    invoice.status_chat_id = status_msg.chat.id
    invoice.status_message_id = status_msg.message_id
    await session.commit()

    await state.update_data(invoice_id=invoice.id, currency=currency)
    await state.set_state(ShamCashAutoStates.waiting_transaction_ref)


@router.message(ShamCashAutoStates.waiting_amount)
async def shamcash_auto_amount_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
    bot,
):
    if not await _require_payment_method(message, "shamcash_auto", state):
        return
    try:
        amount = _parse_positive_amount(message.text)
    except (InvalidOperation, AttributeError):
        await message.answer("⚠️ أرسل رقماً صحيحاً.")
        return

    data = await state.get_data()
    currency = data.get("currency", "USD")
    await _create_shamcash_auto_invoice(message, state, session, db_user, bot, amount, currency)


@router.message(ShamCashAutoStates.waiting_transaction_ref)
async def shamcash_auto_tx_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
    bot,
):
    if not await _require_payment_method(message, "shamcash_auto", state):
        return
    tx_ref = message.text.strip()
    if not tx_ref:
        await message.answer("⚠️ أرسل رقم العملية.")
        return

    reused_result = await session.execute(
        select(AutoInvoice).where(
            AutoInvoice.transaction_ref == tx_ref,
            AutoInvoice.status == AutoInvoiceStatus.PAID,
        )
    )
    if reused_result.scalar_one_or_none() is not None:
        await message.answer("⚠️ رقم العملية مستخدم مسبقاً ولا يمكن استخدامه مرة أخرى.")
        return

    data = await state.get_data()
    invoice_id = data.get("invoice_id")
    invoice = await _get_owned_invoice(session, invoice_id, db_user.id)

    if not invoice:
        await message.answer("⚠️ هذه الفاتورة غير موجودة أو لا تخص حسابك.")
        await state.clear()
        return

    if invoice.status != AutoInvoiceStatus.PENDING:
        await message.answer("⚠️ هذه الفاتورة تمت معالجتها مسبقاً.")
        await state.clear()
        return

    if datetime.utcnow() > invoice.expires_at:
        invoice.status = AutoInvoiceStatus.EXPIRED
        await session.commit()
        await message.answer(
            "⌛ انتهت صلاحية الفاتورة. أنشئ فاتورة جديدة.",
            reply_markup=back_to_main_kb(),
        )
        await state.clear()
        return

    await message.answer("⏳ جاري التحقق من الدفع...")

    try:
        result = await sam_api_client.verify_invoice(
            invoice_id=invoice.external_invoice_id,
            transaction_ref=tx_ref,
        )
    except SamApiExpiredError:
        invoice.status = AutoInvoiceStatus.EXPIRED
        await session.commit()
        await message.answer(
            "⌛ انتهت صلاحية الفاتورة.",
            reply_markup=back_to_main_kb(),
        )
        await state.clear()
        return
    except SamApiError as e:
        logger.error(f"فشل التحقق من فاتورة Sam API: {e}")
        await message.answer(
            f"❌ فشل التحقق:\n<code>{e}</code>\n\nتأكد من رقم العملية وحاول مجدداً.",
        )
        return

    if result.get("verified"):
        # أضف الرصيد أولاً. إذا فشل الحفظ تبقى الفاتورة معلّقة ويمكن
        # إعادة المحاولة، بدلاً من تسجيلها مدفوعة بدون رصيد.
        await BalanceService.add_balance(
            session,
            db_user.id,
            invoice.amount_usd,
            TransactionType.DEPOSIT,
            description=(f"شحن شام كاش تلقائي - فاتورة #{invoice.id}"),
            related_table="auto_invoices",
            related_id=invoice.id,
            payment_reference=f"invoice:{invoice.id}",
        )

        invoice.status = AutoInvoiceStatus.PAID
        invoice.transaction_ref = tx_ref
        invoice.paid_at = datetime.utcnow()
        await session.commit()

        notifier = NotificationService(bot)
        await notifier.notify_admin(
            f"💳 <b>شحن شام كاش تلقائي</b>\n\n"
            f"👤 المستخدم: {db_user.telegram_id} "
            f"(@{db_user.username or '-'})\n"
            f"💵 المبلغ: <b>{invoice.amount_usd}$</b>\n"
            f"💰 الأصلي: {invoice.amount_original} "
            f"{invoice.currency}\n"
            f"🔢 رقم العملية: <code>{tx_ref}</code>"
        )

        await message.answer(
            f"✅ <b>تم شحن رصيدك بنجاح!</b>\n\n"
            f"💰 تمت إضافة <b>{invoice.amount_usd}$</b> "
            f"إلى رصيدك.\n"
            f"يمكنك الآن استخدام رصيدك.",
            reply_markup=back_to_main_kb(),
        )
        await state.clear()
    else:
        msg = result.get("message", "لم يتم العثور على رقم العملية")
        await message.answer(
            f"❌ فشل التحقق:\n<b>{msg}</b>\n\n"
            "تأكد من:\n"
            "• صحة رقم العملية\n"
            "• أن التحويل تم بنجاح\n"
            "• أن المبلغ صحيح\n\n"
            "أرسل رقم العملية مجدداً أو ألغِ الفاتورة."
        )


@router.callback_query(F.data.startswith("sc_verify:"))
async def shamcash_auto_verify_button(
    callback: CallbackQuery,
    state: FSMContext,
    session,
    db_user: User,
):
    if not await _require_payment_method(callback, "shamcash_auto", state):
        return
    invoice_id = int(callback.data.split(":")[1])
    invoice = await _get_owned_invoice(session, invoice_id, db_user.id)
    if invoice is None or invoice.status != AutoInvoiceStatus.PENDING:
        await callback.answer(
            "⚠️ هذه الفاتورة غير موجودة أو تمت معالجتها.",
            show_alert=True,
        )
        return

    await state.update_data(invoice_id=invoice_id)
    await state.set_state(ShamCashAutoStates.waiting_transaction_ref)
    await callback.answer()
    await callback.message.answer("🔢 أرسل رقم العملية للتحقق:")


@router.callback_query(F.data.startswith("sc_cancel:"))
async def shamcash_auto_cancel(
    callback: CallbackQuery,
    session,
    state: FSMContext,
    db_user: User,
):
    invoice_id = int(callback.data.split(":")[1])
    invoice = await _get_owned_invoice(session, invoice_id, db_user.id)

    if invoice is None:
        await callback.answer(
            "⚠️ هذه الفاتورة غير موجودة أو لا تخص حسابك.",
            show_alert=True,
        )
        return

    if invoice.status == AutoInvoiceStatus.PENDING:
        invoice.status = AutoInvoiceStatus.CANCELLED
        await session.commit()

    await callback.answer("❌ تم إلغاء الفاتورة.")
    await state.clear()
    try:
        await callback.message.edit_text(
            "❌ تم إلغاء الفاتورة.",
            reply_markup=back_to_main_kb(),
        )
    except Exception:
        await callback.message.answer(
            "❌ تم إلغاء الفاتورة.",
            reply_markup=back_to_main_kb(),
        )


# ══════════════════════════════════════════
# ══════════════ 4) USDT تلقائي (Plisio) ══════════════
# ══════════════════════════════════════════


@router.callback_query(F.data == "deposit:usdt_auto")
async def usdt_auto_start(callback: CallbackQuery, state: FSMContext):
    if not await _require_payment_method(callback, "usdt_auto", state):
        return
    await callback.answer()
    await state.clear()
    description = await SettingsService.get(
        "payment_usdt_auto_description", "₮ الشحن الفوري عبر USDT"
    )
    await callback.message.edit_text(
        description,
        reply_markup=start_deposit_kb("usdt_auto"),
    )


@router.callback_query(F.data == "deposit_start:usdt_auto")
async def usdt_auto_amount_ask(callback: CallbackQuery, state: FSMContext):
    """Ask for the network before asking for the invoice amount."""
    if not await _require_payment_method(callback, "usdt_auto", state):
        return
    await callback.answer()
    await state.update_data(network=None, plisio_currency=None)
    await callback.message.edit_text(
        "₮ <b>USDT تلقائي</b>\n\n"
        "اختر الشبكة التي ستدفع عبرها:\n\n"
        "🟢 <b>TRC20</b> - موصى بها ورسومها عادة أقل\n"
        "🟡 <b>BEP20</b> - متاحة في إعدادات Plisio الحالية",
        reply_markup=usdt_networks_kb("auto"),
    )
    # Keep the existing amount state for backwards compatibility. The amount
    # handler rejects input until a network has been selected.
    await state.set_state(UsdtAutoStates.waiting_amount)


@router.callback_query(F.data.startswith("usdt_net:auto:"))
async def usdt_auto_network_selected(callback: CallbackQuery, state: FSMContext):
    """Store the selected Plisio currency and then ask for the amount."""
    if not await _require_payment_method(callback, "usdt_auto", state):
        return

    network = callback.data.split(":", 2)[2].upper()
    plisio_currency = _AUTO_USDT_NETWORK_CURRENCIES.get(network)
    if plisio_currency is None:
        await callback.answer(
            "⚠️ هذه الشبكة غير متاحة للدفع التلقائي.",
            show_alert=True,
        )
        return

    await state.update_data(network=network, plisio_currency=plisio_currency)
    await callback.answer()

    min_deposit = await SettingsService.get_decimal(
        "min_deposit_usdt_usd",
        Decimal("2"),
    )
    await callback.message.edit_text(
        f"₮ <b>USDT تلقائي - {network}</b>\n\n"
        f"الحد الأدنى: <b>{min_deposit}$</b>\n\n"
        "أرسل المبلغ بالدولار (مثال: 10):",
        reply_markup=cancel_deposit_kb(),
    )
    await state.set_state(UsdtAutoStates.waiting_amount)


@router.message(UsdtAutoStates.waiting_amount)
async def usdt_auto_amount_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
    bot,
):
    if not await _require_payment_method(message, "usdt_auto", state):
        return

    data = await state.get_data()
    network = str(data.get("network") or "").upper()
    # Derive the provider currency from the validated network instead of
    # trusting a separately stored value that could be stale after a retry.
    plisio_currency = _AUTO_USDT_NETWORK_CURRENCIES.get(network)
    if not plisio_currency:
        await message.answer(
            "⚠️ اختر شبكة الدفع أولاً:",
            reply_markup=usdt_networks_kb("auto"),
        )
        return

    try:
        amount = _parse_positive_amount(message.text)
    except (InvalidOperation, AttributeError):
        await message.answer("⚠️ أرسل رقماً صحيحاً.")
        return

    min_deposit = await SettingsService.get_decimal("min_deposit_usdt_usd", Decimal("2"))
    max_deposit = await SettingsService.get_decimal("plisio_max_amount_usd", Decimal("500"))
    if amount < min_deposit:
        await message.answer(f"⚠️ الحد الأدنى هو {min_deposit}$")
        return
    if amount > max_deposit:
        await message.answer(f"⚠️ الحد الأقصى للشحن هو {max_deposit}$")
        return

    fee_percent = await SettingsService.get_decimal("plisio_fee_percent", Decimal("3.0"))
    amount_with_fee = (amount * (Decimal("1") + fee_percent / Decimal("100"))).quantize(
        Decimal("0.01")
    )

    await message.answer("⏳ جاري إنشاء الفاتورة...")

    order_id = f"user_{db_user.id}_{uuid4().hex[:12]}"

    try:
        payment_data = await plisio_client.create_payment(
            amount=amount_with_fee,
            order_id=order_id,
            currency=plisio_currency,
            order_name=f"Deposit for user {db_user.id}",
            lifetime=max(60, int(settings.PLISIO_INVOICE_EXPIRE_MINUTES) * 60),
        )
    except PlisioError as e:
        logger.error("فشل إنشاء فاتورة Plisio: %s", str(e)[:400])
        await message.answer(
            "❌ فشل إنشاء الفاتورة:\n"
            f"<code>{_safe_plisio_error(e)}</code>\n\n"
            "حاول مجدداً لاحقاً أو استخدم طريقة دفع أخرى.",
            reply_markup=back_to_main_kb(),
        )
        await state.clear()
        return

    # Hosted invoices (the normal non-White-label response) contain only
    # txn_id and invoice_url. wallet_hash/address is optional.
    external_id = str(payment_data.get("uuid") or "").strip()
    address = str(payment_data.get("address") or "").strip() or None
    payment_url = str(payment_data.get("url") or "").strip() or None
    payer_amount = payment_data.get("payer_amount")
    payer_amount_known = payer_amount not in (None, "")

    if not external_id or not payment_url:
        logger.error(
            "استجابة Plisio غير مكتملة: txn_id=%s invoice_url=%s",
            bool(external_id),
            bool(payment_url),
        )
        await message.answer(
            "❌ لم يُرجع مزود الدفع رابط فاتورة صالحاً. "
            "لم يتم إنشاء طلب شحن؛ حاول مجدداً لاحقاً.",
            reply_markup=back_to_main_kb(),
        )
        await state.clear()
        return

    try:
        payer_amount_decimal = (
            Decimal(str(payer_amount)) if payer_amount_known else amount_with_fee
        )
        if not payer_amount_decimal.is_finite() or payer_amount_decimal <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        logger.warning("استجابة Plisio احتوت مبلغ دفع غير صالح؛ سيُستخدم مبلغ المصدر فقط")
        payer_amount_decimal = amount_with_fee
        payer_amount_known = False

    payment_data["payer_amount_known"] = payer_amount_known
    expires_at = _parse_invoice_expiration(
        payment_data.get("expired_at"),
        fallback_minutes=max(1, int(getattr(settings, "PLISIO_INVOICE_EXPIRE_MINUTES", 30))),
    )

    invoice = AutoInvoice(
        user_id=db_user.id,
        method=AutoInvoiceMethod.USDT_AUTO,
        external_invoice_id=external_id,
        amount_usd=amount,
        amount_original=payer_amount_decimal,
        currency="USDT",
        network=network,
        payment_address=address,
        payment_url=payment_url,
        status=AutoInvoiceStatus.PENDING,
        expires_at=expires_at,
        raw_data=json.dumps(payment_data, ensure_ascii=False, default=str),
    )
    session.add(invoice)
    await session.commit()
    await session.refresh(invoice)

    remaining = expires_at - datetime.utcnow()
    minutes = max(0, int(remaining.total_seconds() // 60))

    amount_text = (
        f"💰 المبلغ المطلوب: <b>{payer_amount_decimal} USDT</b>"
        if payer_amount_known
        else "💰 افتح صفحة الدفع لمعرفة قيمة USDT النهائية"
    )
    destination_text = (
        f"📬 عنوان المحفظة:\n<code>{html.escape(address)}</code>"
        if address
        else "📬 تفاصيل العنوان والمبلغ موجودة داخل صفحة الدفع."
    )
    text = (
        f"₮ <b>فاتورة USDT ({network})</b>\n\n"
        f"⏱ الوقت المتبقي: <b>{minutes} دقيقة</b>\n"
        f"{amount_text}\n"
        f"💵 المبلغ المحسوب للشحن: <b>{amount}$</b>\n"
        f"💸 يشمل رسوم: <b>{fee_percent}%</b>\n\n"
        f"{destination_text}\n\n"
        "🌐 اضغط <b>فتح صفحة الدفع</b> لعرض بيانات التحويل الدقيقة.\n\n"
        "⚠️ <b>مهم جداً:</b>\n"
        f"• حوّل عبر شبكة <b>{network}</b> فقط\n"
        "• أرسل المبلغ المحدد داخل صفحة Plisio بالضبط\n"
        "• سيتم فحص الدفع كل 30 ثانية\n"
        "• عند وصول التحويل يُضاف الرصيد تلقائياً\n\n"
        "✨ لا حاجة لإدخال رقم العملية!"
    )

    status_msg = await message.answer(
        text,
        reply_markup=usdt_auto_invoice_kb(invoice.id, payment_url),
    )
    invoice.status_chat_id = status_msg.chat.id
    invoice.status_message_id = status_msg.message_id
    await session.commit()
    await state.clear()


@router.callback_query(F.data.startswith("usdt_check:"))
async def usdt_auto_check_button(callback: CallbackQuery, session, bot, db_user: User):
    if not await _require_payment_method(callback, "usdt_auto"):
        return
    invoice_id = int(callback.data.split(":")[1])
    invoice = await _get_owned_invoice(session, invoice_id, db_user.id)

    if not invoice:
        await callback.answer(
            "⚠️ هذه الفاتورة غير موجودة أو لا تخص حسابك.",
            show_alert=True,
        )
        return

    if invoice.status == AutoInvoiceStatus.PAID:
        await callback.answer("✅ الفاتورة مدفوعة بالفعل.", show_alert=True)
        return

    if invoice.status != AutoInvoiceStatus.PENDING:
        await callback.answer("⚠️ هذه الفاتورة تمت معالجتها.", show_alert=True)
        return

    await callback.answer("⏳ جاري الفحص...")

    try:
        info = await plisio_client.get_payment_info(uuid=invoice.external_invoice_id)
    except PlisioError as e:
        logger.error("فشل فحص فاتورة Plisio: %s", str(e)[:400])
        await callback.message.answer(f"⚠️ خطأ في الفحص: {_safe_plisio_error(e)}")
        return

    status = info.get("status", "process")

    if plisio_client.is_paid_status(status):
        from tasks.invoice_monitor import _process_paid_usdt_invoice

        await _process_paid_usdt_invoice(session, invoice, info, bot)
        await callback.message.answer("✅ تم الدفع! أُضيف الرصيد لحسابك.")
    elif plisio_client.is_failed_status(status):
        invoice.status = AutoInvoiceStatus.FAILED
        await session.commit()
        await callback.message.answer("❌ فشلت الفاتورة.")
    else:
        await callback.message.answer(
            f"⏳ الفاتورة قيد المعالجة (الحالة: {status})\nسيتم إشعارك تلقائياً عند اكتمال الدفع."
        )


@router.callback_query(F.data.startswith("usdt_cancel:"))
async def usdt_auto_cancel(callback: CallbackQuery, session, db_user: User):
    invoice_id = int(callback.data.split(":")[1])
    invoice = await _get_owned_invoice(session, invoice_id, db_user.id)

    if invoice is None:
        await callback.answer(
            "⚠️ هذه الفاتورة غير موجودة أو لا تخص حسابك.",
            show_alert=True,
        )
        return

    if invoice.status == AutoInvoiceStatus.PENDING:
        invoice.status = AutoInvoiceStatus.CANCELLED
        await session.commit()

    await callback.answer("❌ تم إلغاء الفاتورة.")
    try:
        await callback.message.edit_text(
            "❌ تم إلغاء الفاتورة.",
            reply_markup=back_to_main_kb(),
        )
    except Exception:
        await callback.message.answer(
            "❌ تم إلغاء الفاتورة.",
            reply_markup=back_to_main_kb(),
        )