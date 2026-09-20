"""User balance withdrawal requests."""

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from keyboards.main_menu import back_to_main_kb
from services.settings_service import SettingsService
from services.notification_service import NotificationService
from services.withdrawal_service import WithdrawalError, WithdrawalService
from states.states import WithdrawStates

router = Router(name="withdrawal")


def _methods_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 شام كاش", callback_data="withdraw_method:shamcash")],
        [InlineKeyboardButton(text="₮ USDT", callback_data="withdraw_method:usdt")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="back_to_main")],
    ])


def _shamcash_currency_kb(syp_enabled: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="💵 دولار", callback_data="withdraw_currency:USD")]]
    if syp_enabled:
        rows.append([InlineKeyboardButton(text="🇸🇾 ليرة سورية", callback_data="withdraw_currency:SYP")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="withdraw:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _usdt_network_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="TRC20", callback_data="withdraw_net:TRC20")],
        [InlineKeyboardButton(text="BEP20", callback_data="withdraw_net:BEP20")],
        [InlineKeyboardButton(text="ERC20", callback_data="withdraw_net:ERC20")],
    ])


@router.callback_query(F.data == "withdraw:home")
async def withdraw_home(callback: CallbackQuery, state: FSMContext, db_user):
    await state.clear()
    await callback.message.edit_text(
        "💸 <b>سحب الرصيد</b>\n\n"
        f"رصيدك الحالي: <b>{db_user.balance}$</b>\n"
        "اختر طريقة السحب. يتم حجز المبلغ وإرسال الطلب للإدارة حتى يتم الدفع يدوياً.",
        reply_markup=_methods_kb(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("withdraw_method:"))
async def withdraw_method(callback: CallbackQuery, state: FSMContext):
    method = callback.data.split(":")[1]
    await state.update_data(method=method)
    if method == "shamcash":
        enabled = await WithdrawalService.shamcash_syp_enabled()
        await callback.message.edit_text(
            "💵 <b>سحب عبر شام كاش</b>\n\nاختر عملة السحب:",
            reply_markup=_shamcash_currency_kb(enabled),
        )
        await state.set_state(WithdrawStates.waiting_currency)
    else:
        await callback.message.edit_text("₮ <b>سحب USDT</b>\n\nاختر الشبكة:", reply_markup=_usdt_network_kb())
        await state.set_state(WithdrawStates.waiting_network)
    await callback.answer()


@router.callback_query(WithdrawStates.waiting_currency, F.data.startswith("withdraw_currency:"))
async def withdraw_currency(callback: CallbackQuery, state: FSMContext):
    currency = callback.data.split(":")[1]
    await state.update_data(currency=currency)
    minimum = await WithdrawalService.min_amount_usd()
    await state.set_state(WithdrawStates.waiting_amount)
    await callback.message.edit_text(
        f"💰 أرسل مبلغ السحب بالدولار.\nالحد الأدنى: <b>{minimum}$</b>\n"
        "إذا اخترت ليرة سورية سيتم تحويله حسب سعر الصرف اليومي."
    )
    await callback.answer()


@router.callback_query(WithdrawStates.waiting_network, F.data.startswith("withdraw_net:"))
async def withdraw_network(callback: CallbackQuery, state: FSMContext):
    network = callback.data.split(":")[1]
    await state.update_data(currency="USD", network=network)
    minimum = await WithdrawalService.min_amount_usd()
    await state.set_state(WithdrawStates.waiting_amount)
    await callback.message.edit_text(f"💰 أرسل مبلغ السحب بالدولار.\nالحد الأدنى: <b>{minimum}$</b>")
    await callback.answer()


@router.message(WithdrawStates.waiting_amount)
async def withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = Decimal((message.text or "").strip().replace("$", ""))
        if amount <= 0:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("⚠️ أرسل مبلغاً صحيحاً مثل 10")
        return
    await state.update_data(amount_usd=str(amount))
    data = await state.get_data()
    await state.set_state(WithdrawStates.waiting_address)
    if data.get("method") == "usdt":
        await message.answer(f"📬 أرسل عنوان محفظة USDT على شبكة <b>{data.get('network')}</b>:")
    else:
        currency = data.get("currency", "USD")
        await message.answer(f"📬 أرسل عنوان/رقم حساب شام كاش لاستلام <b>{currency}</b>:")


@router.message(WithdrawStates.waiting_address)
async def withdraw_address(message: Message, state: FSMContext, session, db_user, bot):
    address = (message.text or "").strip()
    if len(address) < 4:
        await message.answer("⚠️ العنوان قصير جداً.")
        return
    data = await state.get_data()
    try:
        request = await WithdrawalService.create(
            session,
            user_id=db_user.id,
            method=data.get("method", "shamcash"),
            currency=data.get("currency", "USD"),
            network=data.get("network"),
            amount_usd=Decimal(data.get("amount_usd", "0")),
            payout_address=address,
        )
    except WithdrawalError as exc:
        await message.answer(f"⚠️ {exc}", reply_markup=back_to_main_kb())
        await state.clear()
        return
    await state.clear()
    await message.answer(
        "✅ تم إرسال طلب السحب للإدارة.\n\n"
        f"🆔 الطلب: #{request.id}\n"
        f"💰 المبلغ المحجوز: {request.amount_usd}$\n"
        f"📤 سيصلك: {request.payout_amount} {request.currency}",
        reply_markup=back_to_main_kb(),
    )
    await NotificationService(bot).notify_admin(
        "💸 <b>طلب سحب جديد</b>\n\n"
        f"🆔 الطلب: #{request.id}\n"
        f"👤 المستخدم: <code>{db_user.telegram_id}</code>\n"
        f"الطريقة: {request.method} {request.network or ''}\n"
        f"المبلغ: {request.amount_usd}$\n"
        f"المطلوب دفعه: <b>{request.payout_amount} {request.currency}</b>\n"
        f"العنوان:\n<code>{request.payout_address}</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ تم الدفع", callback_data=f"admin:withdraw_paid:{request.id}")],
            [InlineKeyboardButton(text="❌ رفض وإرجاع الرصيد", callback_data=f"admin:withdraw_reject:{request.id}", style="danger")],
        ]),
    )
