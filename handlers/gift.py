"""بطاقات الهدايا للمستخدمين."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User
from keyboards.gift import gift_cancel_kb, gift_menu_kb
from services.i18n_service import I18nService
from services.gift_service import GiftCodeError, GiftService
from states.states import GiftRedeemStates

router = Router(name="gift")


async def _show_gift_menu(target):
    text = "🎁 <b>بطاقة هدية</b>\n\nإذا لديك كود هدية، يمكنك استبداله وإضافة قيمته إلى رصيدك فوراً."
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=gift_menu_kb())
    else:
        await target.answer(text, reply_markup=gift_menu_kb())


@router.callback_query(F.data == "menu:gift")
async def gift_menu(callback: CallbackQuery, db_user=None):
    await callback.answer()
    await _show_gift_menu(callback)


@router.message(F.text == "🎁 بطاقة هدية")
async def gift_menu_message(message: Message, db_user=None):
    await _show_gift_menu(message)


@router.callback_query(F.data == "gift:redeem")
async def gift_redeem_start(callback: CallbackQuery, state: FSMContext, db_user=None):
    await state.clear()
    await state.set_state(GiftRedeemStates.waiting_code)
    await callback.answer()
    await callback.message.edit_text(
        I18nService.t("gift_redeem_prompt", getattr(db_user, "language_code", "ar") or "ar"),
        reply_markup=gift_cancel_kb(),
    )


@router.message(GiftRedeemStates.waiting_code)
async def gift_code_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
):
    try:
        amount = await GiftService.redeem(session, db_user.id, message.text or "")
    except GiftCodeError as exc:
        await message.answer(f"⚠️ {exc}")
        return
    await state.clear()
    await message.answer(
        I18nService.t("gift_redeemed", db_user.language_code, amount=f"{amount}"),
        reply_markup=gift_menu_kb(),
    )
