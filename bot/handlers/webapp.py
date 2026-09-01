"""استقبال اختيار منتج من Telegram Mini App وتسليمه لمسار البوت الآمن."""

from html import escape
import json

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database.models import ProductStatus, User
from keyboards.games import product_confirm_kb
from services.dynamic_service import DynamicService
from states.states import GamesOrderStates, SMMOrderStates

router = Router(name="webapp")


@router.message(F.web_app_data)
async def webapp_product_selected(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
):
    try:
        data = json.loads(message.web_app_data.data)
        product_id = int(data["product_id"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        await message.answer("⚠️ اختيار المتجر غير صالح.")
        return
    product = await DynamicService.get_product(session, product_id)
    if product is None or product.status != ProductStatus.ACTIVE:
        await message.answer("⚠️ هذا المنتج غير متاح حالياً.")
        return

    await state.clear()
    await state.update_data(product_id=product.id)
    if product.requires_player_id:
        await state.set_state(GamesOrderStates.waiting_player_id)
        await message.answer(f"🎮 <b>{escape(product.name_ar)}</b>\n\nأرسل Player ID لإكمال الطلب:")
    elif product.requires_link:
        await state.set_state(SMMOrderStates.waiting_link)
        await message.answer(f"📈 <b>{escape(product.name_ar)}</b>\n\nأرسل الرابط لإكمال الطلب:")
    else:
        await message.answer(
            f"📦 <b>{escape(product.name_ar)}</b>\n"
            f"💰 السعر: <b>{product.price_usd}$</b>\n\n"
            "هل تريد تأكيد الشراء؟",
            reply_markup=product_confirm_kb(
                product.id,
                product.sub_category_id,
            ),
        )
