"""
الإذاعة الجماعية.
"""

import asyncio

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from database.models import User, BroadcastLog
from states.states import AdminBroadcastStates
from keyboards.admin import admin_back_kb
from filters.admin_filter import IsAdmin

router = Router(name="admin_broadcast")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:broadcast")
async def broadcast_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📢 <b>إذاعة جماعية</b>\n\n"
        "أرسل الرسالة (نص أو صورة مع كابشن) "
        "التي تريد إذاعتها لكل المستخدمين:\n\n"
        "⚠️ سيتم إرسالها لكل المستخدمين غير المحظورين.",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminBroadcastStates.waiting_content)


@router.message(AdminBroadcastStates.waiting_content)
async def broadcast_content_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
    bot,
):
    result = await session.execute(select(User).where(User.is_banned.is_(False)))
    users = result.scalars().all()

    progress_msg = await message.answer(
        f"📢 جاري الإرسال لـ {len(users)} مستخدم...\n⏳ يرجى الانتظار."
    )

    sent, failed = 0, 0
    for user in users:
        try:
            if message.photo:
                await bot.send_photo(
                    user.telegram_id,
                    message.photo[-1].file_id,
                    caption=message.caption,
                )
            elif message.video:
                await bot.send_video(
                    user.telegram_id,
                    message.video.file_id,
                    caption=message.caption,
                )
            elif message.document:
                await bot.send_document(
                    user.telegram_id,
                    message.document.file_id,
                    caption=message.caption,
                )
            else:
                await bot.send_message(
                    user.telegram_id,
                    message.text or message.caption or "",
                )
            sent += 1
        except Exception:
            failed += 1

        if (sent + failed) % 30 == 0:
            await asyncio.sleep(1)

    session.add(
        BroadcastLog(
            admin_id=db_user.id,
            total_sent=sent,
            total_failed=failed,
        )
    )
    await session.commit()

    try:
        await progress_msg.edit_text(
            f"✅ تم الإرسال.\n\n📤 نجح: {sent}\n❌ فشل: {failed}\n📊 الإجمالي: {len(users)}"
        )
    except Exception:
        await message.answer(f"✅ تم الإرسال.\n📤 نجح: {sent} | ❌ فشل: {failed}")

    await state.clear()
