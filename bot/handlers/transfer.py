"""
تحويل الرصيد بين المستخدمين.
"""

import logging
from decimal import Decimal

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select

from database.models import TransactionType, User
from services.balance_service import BalanceService, InsufficientBalanceError
from services.input_validation_service import InputValidationError, InputValidationService
from services.notification_service import NotificationService
from services.feature_service import FeatureService
from services.settings_service import SettingsService
from states.states import TransferStates
from services.currency_service import CurrencyService
from services.i18n_service import I18nService
from keyboards.main_menu import back_to_main_kb

logger = logging.getLogger(__name__)
router = Router(name="transfer")


@router.message(F.text == "🔄 تحويل الرصيد")
async def transfer_start(message: Message, state: FSMContext, db_user: User):
    await state.clear()
    await _send_transfer_card(message, db_user)
    await state.set_state(TransferStates.waiting_recipient_id)


@router.callback_query(F.data == "menu:transfer")
async def transfer_start_cb(callback: CallbackQuery, state: FSMContext, db_user: User):
    await callback.answer()
    await state.clear()
    await _send_transfer_card(callback.message, db_user)
    await state.set_state(TransferStates.waiting_recipient_id)


async def _send_transfer_card(message, db_user):
    """بطاقة تحويل الرصيد بلغة المستخدم وعملة عرضه."""
    language = db_user.language_code
    balance_display = await CurrencyService.format_dual(db_user.balance, db_user, None)
    await message.answer(
        I18nService.t("transfer_card", language, balance=balance_display),
        reply_markup=back_to_main_kb(language),
    )


@router.message(TransferStates.waiting_recipient_id)
async def transfer_recipient_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
):
    try:
        recipient_tg_id = int(message.text.strip())
    except ValueError:
        await message.answer("⚠️ الرجاء إرسال آيدي صحيح (أرقام فقط).")
        return

    if recipient_tg_id == db_user.telegram_id:
        await message.answer("⚠️ لا يمكنك التحويل لنفسك.")
        return

    result = await session.execute(select(User).where(User.telegram_id == recipient_tg_id))
    recipient = result.scalar_one_or_none()
    if recipient is None:
        await message.answer("⚠️ لا يوجد مستخدم بهذا الآيدي في البوت.")
        return

    await state.update_data(
        recipient_id=recipient.id,
        recipient_tg_id=recipient.telegram_id,
    )
    await message.answer(
        f"💰 رصيدك الحالي: <b>{db_user.balance:.2f}$</b>\nأرسل المبلغ المراد تحويله بالدولار:"
    )
    await state.set_state(TransferStates.waiting_amount)


@router.message(TransferStates.waiting_amount)
async def transfer_amount_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
    bot,
):
    try:
        amount = InputValidationService.positive_money(message.text)
    except InputValidationError:
        await message.answer("⚠️ المبلغ يجب أن يكون رقماً صحيحاً أكبر من صفر.")
        return

    data = await state.get_data()
    recipient_id = data["recipient_id"]
    recipient_tg_id = data["recipient_tg_id"]

    # ── عمولة التحويل وحدوده (يتحكم بهما الأدمن من لوحة «اقتصاد النقاط») ──
    fee_percent = Decimal("0")
    fee_amount = Decimal("0")
    net_amount = amount
    if await FeatureService.enabled("transfer_fee"):
        low = Decimal(str(await FeatureService.config_decimal("transfer_fee", "min_amount_usd", 1.0)))
        high = Decimal(str(await FeatureService.config_decimal("transfer_fee", "max_amount_usd", 1000.0)))
        if amount < low or amount > high:
            await message.answer(f"⚠️ المبلغ يجب أن يكون بين {low}$ و{high}$.")
            return
        min_age = await FeatureService.config_int("transfer_fee", "require_min_account_age_hours", 0)
        if min_age > 0 and db_user.joined_at is not None:
            from datetime import datetime

            age_hours = (datetime.utcnow() - db_user.joined_at).total_seconds() / 3600
            if age_hours < min_age:
                await message.answer(
                    f"⚠️ التحويل يتطلب حساباً بعمر {min_age} ساعة على الأقل."
                )
                await state.clear()
                return
        fee_percent = Decimal(str(await FeatureService.config_decimal("transfer_fee", "fee_percent", 1.0)))
        fee_amount = (amount * fee_percent / Decimal("100")).quantize(Decimal("0.0001"))
        net_amount = amount - fee_amount
        if net_amount <= 0:
            await message.answer("⚠️ المبلغ صغير جداً بحيث تغطيه العمولة بالكامل.")
            return
        await FeatureService.track("transfer_fee", "transfer", user_id=db_user.id, value=str(fee_amount))

    # فحص الرصيد قبل أي خصم: لو لا يكفي للمبلغ الإجمالي (المبلغ + العمولة)
    # نرفض قبل الخصم، فلا يُحتاج استرجاع عمولة ولا يُترك رصيد ناقص.
    if not await BalanceService.check_sufficient(session, db_user.id, amount):
        await message.answer("⚠️ رصيدك غير كافٍ لإتمام هذا التحويل مع العمولة.")
        await state.clear()
        return

    try:
        # نخصم العمولة أولاً كإيراد للمنصة، ثم نحوّل الصافي للمستلم.
        # إن فشل التحويل تُردّ العمولة فوراً فلا يُخصم المستخدم مرتين.
        if fee_amount > 0:
            await BalanceService.deduct_balance(
                session,
                db_user.id,
                fee_amount,
                TransactionType.PURCHASE,
                description=f"عمولة تحويل {fee_percent}% إلى {recipient_tg_id}",
                related_table="users",
                related_id=recipient_id,
            )
        try:
            _, _, _ = await BalanceService.transfer(
                session,
                from_user_id=db_user.id,
                to_user_id=recipient_id,
                amount=net_amount,
            )
        except InsufficientBalanceError:
            if fee_amount > 0:
                await BalanceService.add_balance(
                    session,
                    db_user.id,
                    fee_amount,
                    TransactionType.REFUND,
                    description="إرجاع عمولة تحويل لم يكتمل",
                    payment_reference=f"transfer_fee_refund:{db_user.id}:{recipient_id}:{int(amount * 10000)}",
                )
            raise
    except InsufficientBalanceError:
        await message.answer("⚠️ رصيدك غير كافٍ لإتمام هذا التحويل مع العمولة.")
        await state.clear()
        return
    except Exception:
        # The transfer method commits debit, credit and ledger rows as one
        # transaction. No separate refund is needed and no balance can be
        # lost between two commits.
        logger.exception("فشل التحويل إلى %s", recipient_tg_id)
        await message.answer("⚠️ تعذّر إتمام التحويل. لم يتم خصم أي مبلغ، حاول لاحقاً.")
        await state.clear()
        return

    notifier = NotificationService(bot)
    await message.answer(
        f"✅ تم تحويل <b>{amount:.2f}$</b> إلى المستخدم {recipient_tg_id} بنجاح."
        + (f"\n💸 العمولة: {fee_amount:.4f}$ ({fee_percent}%)\n💰 وصل للمستلم: {net_amount:.4f}$"
           if fee_amount > 0 else "")
    )
    await notifier.notify_user(
        recipient_tg_id,
        f"💰 استلمت تحويلاً بقيمة <b>{net_amount:.2f}$</b> من المستخدم {db_user.telegram_id}.",
    )

    large_threshold = await SettingsService.get_decimal(
        "large_transaction_threshold_usd", Decimal("20")
    )
    if amount >= large_threshold:
        await notifier.notify_admin(
            f"🚨 <b>تحويل كبير!</b>\n\n"
            f"من: {db_user.telegram_id}\n"
            f"إلى: {recipient_tg_id}\n"
            f"المبلغ: {amount:.2f}$"
        )

    await state.clear()
