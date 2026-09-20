"""
الشحن برصيد الجوال (ميزة mobile_credit_deposit).

يقوم المستخدم بإرسال:
1) رقم الجوال الذي سيُشحَن منه الرصيد
2) المبلغ بالدولار (ضمن حدود الميزة)
3) لقطة من عملية التحويل

فيُصبح طلباً قيد المراجعة كأي طلب شحن يدوي، ويصل للوحة الأدمن
بأزرار قبول/رفض.
"""

import logging

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from decimal import Decimal, InvalidOperation

from database.models import DepositRequest, DepositStatus
from services.payment_method_service import payment_method_enabled
from services.settings_service import SettingsService
from services.feature_service import FeatureService
from services.notification_service import NotificationService
from services.input_validation_service import InputValidationService
from states.states import MobileCreditDepositStates
from keyboards.admin import deposit_decision_kb
from keyboards.deposit_methods import start_deposit_kb, amount_presets_kb, cancel_deposit_kb
from keyboards.main_menu import back_to_main_kb

logger = logging.getLogger(__name__)

router = Router(name="deposit_credit")


async def _require_feature(callback: CallbackQuery, state: FSMContext) -> bool:
    if await payment_method_enabled("mobile_credit") and await FeatureService.enabled("mobile_credit_deposit"):
        return True
    await state.clear()
    await callback.answer("⚠️ طريقة الدفع موقوفة حالياً.", show_alert=True)
    return False


def _parse_amount(value: str | None) -> Decimal | None:
    try:
        raw = (value or "").replace(",", ".").strip()
        amount = Decimal(raw)
    except (InvalidOperation, AttributeError):
        return None
    if amount <= 0 or amount.is_nan():
        return None
    return amount


# ══════════════ الدخول من قائمة طرق الشحن ══════════════


@router.callback_query(F.data == "deposit:mobile_credit")
async def mobile_credit_start(callback: CallbackQuery, state: FSMContext):
    if not await _require_feature(callback, state):
        return
    await callback.answer()
    await state.clear()
    description = await SettingsService.get(
        "payment_mobile_credit_description",
        "📲 <b>الشحن برصيد الجوال</b>\n\n"
        "ارسل لنا رقم جوالك والمبلغ المراد شحنه ولقطة من عملية التحويل "
        "وسنضيف الرصيد إلى حسابك.",
    )
    await callback.message.edit_text(
        description,
        reply_markup=start_deposit_kb("mobile_credit"),
    )


@router.callback_query(F.data == "deposit_start:mobile_credit")
async def mobile_credit_provider_number_ask(callback: CallbackQuery, state: FSMContext):
    if not await _require_feature(callback, state):
        return
    await callback.answer()
    await callback.message.edit_text(
        "📲 <b>الشحن برصيد الجوال</b>\n\n"
        "أرسل <b>رقم الجوال</b> الذي ستُنفَّذ منه عملية التحويل:\n"
        "(مثال: 0933556677)",
        reply_markup=cancel_deposit_kb(),
    )
    await state.set_state(MobileCreditDepositStates.waiting_provider_number)


@router.message(MobileCreditDepositStates.waiting_provider_number)
async def mobile_credit_provider_number_received(message: Message, state: FSMContext):
    phone = (message.text or "").strip()
    if not phone.isdigit() or not (8 <= len(phone) <= 14):
        await message.answer("⚠️ الرجاء إرسال رقم جوال صحيح (أرقام فقط، من 8 إلى 14 خانة).")
        return

    min_amount = await FeatureService.config_int("mobile_credit_deposit", "min_amount_usd", 1)
    max_amount = await FeatureService.config_int("mobile_credit_deposit", "max_amount_usd", 250)

    await state.update_data(provider_number=phone)
    await state.set_state(MobileCreditDepositStates.waiting_amount)
    await message.answer(
        f"📲 رقم الجوال: <code>{phone}</code>\n\n"
        f"💵 أرسل <b>المبلغ بالدولار</b> الذي تم شحنه:\n"
        f"(من {min_amount}$ إلى {max_amount}$)\n\n"
        "أو اختر من المبالغ الجاهزة:",
        reply_markup=amount_presets_kb("mobile_credit", "USD", "deposit:mobile_credit"),
    )


@router.message(MobileCreditDepositStates.waiting_amount)
async def mobile_credit_amount_received(message: Message, state: FSMContext):
    amount = _parse_amount(message.text)
    if amount is None:
        await message.answer("⚠️ أرسل رقماً صحيحاً بالدولار، مثال: 10")
        return
    await _accept_mobile_credit_amount(message, state, amount)


async def _accept_mobile_credit_amount(target, state: FSMContext, amount: Decimal) -> bool:
    min_amount = await FeatureService.config_int("mobile_credit_deposit", "min_amount_usd", 1)
    max_amount = await FeatureService.config_int("mobile_credit_deposit", "max_amount_usd", 250)
    if amount < min_amount or amount > max_amount:
        await target.answer(f"⚠️ المبلغ يجب أن يكون بين {min_amount}$ و {max_amount}$.")
        return False

    await state.update_data(amount_usd=str(amount))
    await state.set_state(MobileCreditDepositStates.waiting_proof_photo)
    await target.answer(
        f"💵 المبلغ: <b>{amount}$</b>\n\n"
        "📸 الآن أرسل <b>لقطة من عملية التحويل</b> (إثبات الرسيد):\n\n"
        "سيصل الطلب فوراً للوحة الأدمن بانتظار الموافقة.",
        reply_markup=cancel_deposit_kb(),
    )
    return True


@router.message(MobileCreditDepositStates.waiting_proof_photo, F.photo)
async def mobile_credit_photo_received(
    message: Message,
    state: FSMContext,
    session,
    db_user,
    bot,
):
    data = await state.get_data()
    provider_number = data.get("provider_number", "")
    amount_usd = Decimal(data.get("amount_usd", "0"))
    photo_file_id = message.photo[-1].file_id

    deposit = DepositRequest(
        user_id=db_user.id,
        amount_usd=amount_usd,
        proof_photo_file_id=photo_file_id,
        proof_tx_number=provider_number,
        payment_method="Mobile Credit",
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
        tx_number=provider_number,
        deposit_id=deposit.id,
        photo_file_id=photo_file_id,
        reply_markup=deposit_decision_kb(deposit.id),
    )

    if sent_msg_id:
        deposit.admin_chat_message_id = sent_msg_id
        await session.commit()

    await message.answer(
        "✅ تم إرسال طلب الشحن برصيد الجوال بنجاح!\n\n"
        f"📱 رقم الجوال: <code>{provider_number}</code>\n"
        f"💰 المبلغ: <b>{amount_usd}$</b>\n\n"
        "⏳ بانتظار موافقة الإدارة.\n"
        "سيصلك إشعار فور مراجعة طلبك.",
        reply_markup=back_to_main_kb(),
    )
    await state.clear()


@router.message(MobileCreditDepositStates.waiting_proof_photo)
async def mobile_credit_photo_invalid(message: Message):
    await message.answer("⚠️ الرجاء إرسال صورة إثبات عملية التحويل (وليس نصاً).")