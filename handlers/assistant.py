"""مساعد ذكي لاختيار المنتجات من الكتالوج."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from keyboards.assistant import assistant_start_kb
from keyboards.games import product_search_results_kb
from services.assistant_service import AssistantService
from states.states import AssistantStates

router = Router(name="assistant")


async def _show_assistant_prompt(target, state: FSMContext):
    await state.clear()
    await state.set_state(AssistantStates.waiting_request)
    text = (
        "🧠 <b>المساعد الذكي</b>\n\n"
        "اكتب طلبك بطريقتك الطبيعية، مثلاً:\n"
        "• أريد متابعين إنستغرام رخيصين\n"
        "• أفضل شحن PUBG\n"
        "• اشتراك ذكاء اصطناعي\n\n"
        "سأبحث في كل الكتالوج وأرتب الاقتراحات لك."
    )
    if isinstance(target, CallbackQuery):
        await target.answer()
        await target.message.edit_text(text, reply_markup=assistant_start_kb())
    else:
        await target.answer(text, reply_markup=assistant_start_kb())


@router.callback_query(F.data == "menu:assistant")
async def assistant_start(callback: CallbackQuery, state: FSMContext):
    await _show_assistant_prompt(callback, state)


@router.message(AssistantStates.waiting_request)
async def assistant_request_received(
    message: Message,
    state: FSMContext,
    session,
):
    request = (message.text or "").strip()
    if len(request) < 2:
        await message.answer("⚠️ اكتب وصفاً أطول قليلاً لما تحتاجه.")
        return
    products, reason = await AssistantService.recommend(session, request)
    await state.clear()
    if not products:
        await message.answer(
            "🧠 لم أجد منتجات مناسبة حالياً. يمكنك طلب الخدمة ليصوت عليها المستخدمون.",
            reply_markup=assistant_start_kb(),
        )
        return
    await message.answer(
        f"🧠 <b>اقتراحاتي لك</b>\n\n{escape(reason)}\nوجدت {len(products)} نتيجة:",
        reply_markup=product_search_results_kb(products),
    )


@router.message(F.text == "🧠 المساعد الذكي")
async def assistant_message(message: Message, state: FSMContext):
    await _show_assistant_prompt(message, state)
