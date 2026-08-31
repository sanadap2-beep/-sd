"""
هاندلر شحن الرصيد الرئيسي.
يعرض قائمة طرق الدفع الست ويعالج:
1) نجوم تليجرام (تلقائي)
2) الإيداع اليدوي التقليدي (للتوافق مع القديم)
3) القبول/الرفض من الأدمن

طرق الدفع الست الأخرى (شام كاش يدوي، شام كاش تلقائي،
USDT يدوي، USDT تلقائي، طرق أخرى) في handlers/deposit_methods.py
"""
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, PreCheckoutQuery
from sqlalchemy import select
from database.models import DepositRequest, DepositStatus, User, TransactionType
from services.settings_service import SettingsService
from services.balance_service import BalanceService
from services.notification_service import NotificationService
from services.stars_service import StarsService
from services.dynamic_service import DynamicService
from handlers.deposit_methods import _payment_method_enabled
from services.input_validation_service import InputValidationError, InputValidationService
from states.states import DepositStates
from keyboards.admin import deposit_decision_kb
from services.i18n_service import I18nService
from keyboards.main_menu import deposit_menu_kb, stars_packages_kb, back_to_main_kb
logger = logging.getLogger(__name__)
router = Router(name='deposit')


def _auto_lang(scope=None) -> str:
    user = (scope or {}).get("db_user")
    if user is None:
        callback = (scope or {}).get("callback")
        user = getattr(callback, "from_user", None)
    return getattr(user, "language_code", "ar") or "ar"

def _parse_positive_amount(value: str | None) -> Decimal:
    """Parse a finite positive monetary amount from user input."""
    if not value:
        raise InvalidOperation
    try:
        return InputValidationService.positive_money(value)
    except InputValidationError:
        raise InvalidOperation from None

async def _get_admin_user(session, telegram_id: int) -> User | None:
    """Resolve the Telegram admin to the internal users.id value."""
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user and user.is_admin:
        return user
    return None

@router.message(F.text == '💰 شحن الرصيد')
async def deposit_start(message: Message, state: FSMContext, db_user=None):
    await state.clear()
    await _show_deposit_methods(message, db_user)

@router.callback_query(F.data == 'menu:deposit')
async def deposit_start_cb(callback: CallbackQuery, state: FSMContext, db_user=None):
    await callback.answer()
    await state.clear()
    await _show_deposit_methods(callback, db_user)

async def _show_deposit_methods(target, db_user=None):
    """يعرض قائمة طرق الدفع الست بلغة المستخدم."""
    shamcash_manual = await _payment_method_enabled('shamcash_manual')
    stars = await _payment_method_enabled('stars')
    usdt_manual = await _payment_method_enabled('usdt_manual')
    shamcash_auto = await _payment_method_enabled('shamcash_auto')
    usdt_auto = await _payment_method_enabled('usdt_auto')
    other = await _payment_method_enabled('other')
    language = getattr(db_user, 'language_code', 'ar') or 'ar'
    text = I18nService.t('deposit_title', language)
    kb = deposit_menu_kb(shamcash_manual_enabled=shamcash_manual, stars_enabled=stars, usdt_manual_enabled=usdt_manual, shamcash_auto_enabled=shamcash_auto, usdt_auto_enabled=usdt_auto, other_enabled=other, language=language)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
        except Exception:
            await target.message.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)

@router.callback_query(F.data == 'deposit:stars')
async def deposit_stars_menu(callback: CallbackQuery, session, db_user=None):
    if not await SettingsService.get_bool('payment_stars_enabled', False):
        await callback.answer(I18nService.t('payment_method_disabled', _auto_lang(locals())), show_alert=True)
        return
    await callback.answer()
    packages = await DynamicService.get_active_stars_packages(session)
    language = getattr(db_user, 'language_code', 'ar') or 'ar'
    if not packages:
        await callback.message.edit_text(I18nService.t('no_stars_packages', language), reply_markup=back_to_main_kb(language))
        return
    await callback.message.edit_text(I18nService.t('stars_menu', language), reply_markup=stars_packages_kb(packages, language))

@router.callback_query(F.data.startswith('stars_buy:'))
async def stars_buy(callback: CallbackQuery, session, bot, db_user=None):
    if not await SettingsService.get_bool('payment_stars_enabled', False):
        await callback.answer(I18nService.t('payment_method_disabled', _auto_lang(locals())), show_alert=True)
        return
    package_id = int(callback.data.split(':')[1])
    await callback.answer()
    success = await StarsService.send_stars_invoice(bot=bot, chat_id=callback.message.chat.id, package_id=package_id, session=session)
    if not success:
        await callback.message.answer(I18nService.t('invoice_failed', getattr(db_user, 'language_code', 'ar') or 'ar'))

@router.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    if not await SettingsService.get_bool('payment_stars_enabled', False):
        await pre_checkout_query.answer(
            ok=False,
            error_message=I18nService.t(
                'payment_method_disabled',
                getattr(pre_checkout_query.from_user, 'language_code', 'ar'),
            ),
        )
        return
    await StarsService.handle_pre_checkout(pre_checkout_query)

@router.message(F.successful_payment)
async def successful_payment_handler(message: Message, session, db_user, bot):
    await StarsService.handle_successful_payment(message=message, session=session, db_user=db_user, bot=bot)

@router.callback_query(F.data.startswith('deposit_accept:'))
async def deposit_accept(callback: CallbackQuery, session, bot):
    if not callback.from_user:
        return
    admin_user = await _get_admin_user(session, callback.from_user.id)
    if admin_user is None:
        await callback.answer(I18nService.t('ux_deposit_181_1', _auto_lang(locals())), show_alert=True)
        return
    deposit_id = int(callback.data.split(':')[1])
    deposit = await session.get(DepositRequest, deposit_id)
    if deposit is None or deposit.status != DepositStatus.PENDING:
        await callback.answer(I18nService.t('ux_deposit_191_2', _auto_lang(locals())), show_alert=True)
        return
    user = await BalanceService.add_balance(session, deposit.user_id, deposit.amount_usd, TransactionType.DEPOSIT, description=f'شحن رصيد - طلب #{deposit.id}', related_table='deposit_requests', related_id=deposit.id, payment_reference=f'deposit:{deposit.id}')
    deposit.status = DepositStatus.APPROVED
    deposit.admin_id = admin_user.id
    deposit.processed_at = datetime.utcnow()
    await session.commit()
    notifier = NotificationService(bot)
    await notifier.notify_deposit_approved(user_telegram_id=user.telegram_id, amount_usd=str(deposit.amount_usd))
    try:
        await callback.message.edit_caption(caption=(callback.message.caption or '') + f'\n\n✅ <b>تم القبول</b> بواسطة {callback.from_user.full_name}', reply_markup=None)
    except Exception:
        try:
            await callback.message.edit_text((callback.message.text or '') + I18nService.t('ux_deposit_229_3', _auto_lang(locals())), reply_markup=None)
        except Exception:
            pass
    await callback.answer(I18nService.t('ux_deposit_235_4', _auto_lang(locals())))
    logger.info(f'الأدمن {callback.from_user.id} قبل إيداع #{deposit.id} ({deposit.amount_usd}$)')

@router.callback_query(F.data.startswith('deposit_reject:'))
async def deposit_reject(callback: CallbackQuery, session, bot):
    if not callback.from_user:
        return
    admin_user = await _get_admin_user(session, callback.from_user.id)
    if admin_user is None:
        await callback.answer(I18nService.t('ux_deposit_247_5', _auto_lang(locals())), show_alert=True)
        return
    deposit_id = int(callback.data.split(':')[1])
    deposit = await session.get(DepositRequest, deposit_id)
    if deposit is None or deposit.status != DepositStatus.PENDING:
        await callback.answer(I18nService.t('ux_deposit_257_6', _auto_lang(locals())), show_alert=True)
        return
    deposit.status = DepositStatus.REJECTED
    deposit.admin_id = admin_user.id
    deposit.processed_at = datetime.utcnow()
    await session.commit()
    user = await session.get(User, deposit.user_id)
    notifier = NotificationService(bot)
    await notifier.notify_deposit_rejected(user_telegram_id=user.telegram_id)
    try:
        await callback.message.edit_caption(caption=(callback.message.caption or '') + f'\n\n❌ <b>تم الرفض</b> بواسطة {callback.from_user.full_name}', reply_markup=None)
    except Exception:
        try:
            await callback.message.edit_text((callback.message.text or '') + I18nService.t('ux_deposit_284_7', _auto_lang(locals())), reply_markup=None)
        except Exception:
            pass
    await callback.answer(I18nService.t('ux_deposit_290_8', _auto_lang(locals())))
    logger.info(f'الأدمن {callback.from_user.id} رفض إيداع #{deposit.id}')

@router.message(DepositStates.waiting_amount)
async def deposit_amount_received(message: Message, state: FSMContext):
    """
    ملاحظة: هذا للتوافق فقط مع أي مستخدم عالق في
    الحالة القديمة. الطرق الجديدة في deposit_methods.py
    """
    if not await SettingsService.get_bool('payment_other_enabled', False):
        await state.clear()
        await message.answer(I18nService.t('payment_method_disabled', _auto_lang(locals())))
        return
    try:
        amount = _parse_positive_amount(message.text)
    except (InvalidOperation, AttributeError):
        await message.answer(I18nService.t('ux_deposit_306_9', _auto_lang(locals())))
        return
    min_deposit = await SettingsService.get_decimal('min_deposit_shamcash_usd', Decimal('0.5'))
    if amount < min_deposit:
        await message.answer(f"{I18nService.t('ux_deposit_311_10', _auto_lang(locals()))}{min_deposit}$")
        return
    await state.update_data(amount_usd=str(amount))
    payment_text = await SettingsService.get('payment_method_text', 'سيتم إضافة طريقة الدفع قريباً')
    await message.answer(f"{I18nService.t('ux_deposit_318_11', _auto_lang(locals()))}{payment_text}{I18nService.t('ux_deposit_318_12', _auto_lang(locals()))}")
    await state.set_state(DepositStates.waiting_proof_photo)

@router.message(DepositStates.waiting_proof_photo, F.photo)
async def deposit_photo_received(message: Message, state: FSMContext):
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await message.answer(I18nService.t('ux_deposit_326_13', _auto_lang(locals())))
    await state.set_state(DepositStates.waiting_tx_number)

@router.message(DepositStates.waiting_proof_photo)
async def deposit_photo_invalid(message: Message):
    await message.answer(I18nService.t('ux_deposit_332_14', _auto_lang(locals())))

@router.message(DepositStates.waiting_tx_number)
async def deposit_tx_number_received(message: Message, state: FSMContext, session, db_user: User, bot):
    data = await state.get_data()
    amount_usd = Decimal(data['amount_usd'])
    photo_file_id = data['photo_file_id']
    tx_number = message.text.strip()
    duplicate = await session.execute(select(DepositRequest.id).where(DepositRequest.proof_tx_number == tx_number, DepositRequest.status.in_([DepositStatus.PENDING, DepositStatus.APPROVED])).limit(1))
    if duplicate.scalar_one_or_none() is not None:
        await message.answer(I18nService.t('ux_deposit_357_15', _auto_lang(locals())))
        await state.clear()
        return
    deposit = DepositRequest(user_id=db_user.id, amount_usd=amount_usd, proof_photo_file_id=photo_file_id, proof_tx_number=tx_number, payment_method='Manual (Legacy)', status=DepositStatus.PENDING)
    session.add(deposit)
    await session.commit()
    await session.refresh(deposit)
    notifier = NotificationService(bot)
    sent_msg_id = await notifier.notify_new_deposit(user_telegram_id=db_user.telegram_id, username=db_user.username, amount_usd=str(amount_usd), tx_number=tx_number, deposit_id=deposit.id, photo_file_id=photo_file_id, reply_markup=deposit_decision_kb(deposit.id))
    if sent_msg_id:
        deposit.admin_chat_message_id = sent_msg_id
        await session.commit()
        logger.info(f'إشعار إيداع #{deposit.id} أُرسل بنجاح')
    else:
        logger.error(f'فشل إرسال إشعار إيداع #{deposit.id} للأدمن!')
    await message.answer(I18nService.t('ux_deposit_391_16', _auto_lang(locals())))
    await state.clear()
