"""تقييمات المنتجات بعد الشراء."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import UnifiedOrder, UnifiedOrderStatus, User
from keyboards.reviews import rating_kb, review_cancel_kb
from services.review_service import ReviewError, ReviewService
from states.states import ReviewStates

router = Router(name="reviews")


@router.callback_query(F.data.startswith("review:start:"))
async def review_start(
    callback: CallbackQuery,
    session,
    db_user: User,
    state: FSMContext,
):
    order_id = int(callback.data.split(":")[2])
    order = await session.get(UnifiedOrder, order_id)
    if order is None or order.user_id != db_user.id or order.status != UnifiedOrderStatus.COMPLETED:
        await callback.answer("⚠️ لا يمكن تقييم هذا الطلب.", show_alert=True)
        return
    await state.clear()
    await state.update_data(review_order_id=order_id)
    await state.set_state(ReviewStates.waiting_rating)
    await callback.answer()
    await callback.message.edit_text(
        "⭐ <b>قيّم تجربتك</b>\n\nاختر تقييماً من نجمة إلى خمس نجوم:",
        reply_markup=rating_kb(order_id),
    )


@router.callback_query(F.data.startswith("review:rating:"))
async def review_rating(
    callback: CallbackQuery,
    session,
    db_user: User,
    state: FSMContext,
):
    parts = callback.data.split(":")
    order_id = int(parts[2])
    rating = int(parts[3])
    order = await session.get(UnifiedOrder, order_id)
    if order is None or order.user_id != db_user.id or order.status != UnifiedOrderStatus.COMPLETED:
        await callback.answer("⚠️ الطلب غير صالح.", show_alert=True)
        return
    await state.update_data(review_order_id=order_id, review_rating=rating)
    await state.set_state(ReviewStates.waiting_comment)
    await callback.answer()
    await callback.message.edit_text(
        f"⭐ اخترت {rating}/5\n\nأرسل تعليقاً مختصراً، أو اضغط تخطي:",
        reply_markup=review_cancel_kb(),
    )


@router.message(ReviewStates.waiting_comment)
async def review_comment(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
):
    data = await state.get_data()
    comment = (message.text or "").strip()
    if comment == "-":
        comment = None
    try:
        review = await ReviewService.create(
            session,
            db_user.id,
            int(data["review_order_id"]),
            int(data["review_rating"]),
            comment,
        )
    except ReviewError as exc:
        await message.answer(f"⚠️ {exc}")
        await state.clear()
        return
    await state.clear()
    await message.answer(
        f"✅ شكراً لتقييمك!\n\nتقييمك: {'⭐' * review.rating}\n{escape(review.comment or '')}",
    )


@router.callback_query(F.data == "review:skip_comment")
async def review_skip_comment(
    callback: CallbackQuery,
    state: FSMContext,
    session,
    db_user: User,
):
    data = await state.get_data()
    comment = None
    try:
        review = await ReviewService.create(
            session,
            db_user.id,
            int(data["review_order_id"]),
            int(data["review_rating"]),
            comment,
        )
    except (ReviewError, KeyError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        await state.clear()
        return
    await state.clear()
    await callback.answer("✅ تم حفظ تقييمك.", show_alert=True)
    await callback.message.edit_text(f"✅ تم حفظ تقييمك: {'⭐' * review.rating}")
