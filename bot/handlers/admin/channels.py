"""
إدارة قنوات الاشتراك الإجباري.
"""

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from database.models import MandatoryChannel
from states.states import AdminChannelStates
from keyboards.admin import admin_channels_kb, admin_back_kb
from filters.admin_filter import IsAdmin

router = Router(name="admin_channels")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:channels")
async def channels_list(callback: CallbackQuery, session):
    result = await session.execute(
        select(MandatoryChannel).where(MandatoryChannel.is_active.is_(True))
    )
    channels = result.scalars().all()
    text = "📌 <b>قنوات الاشتراك الإجباري:</b>\n\nيمكن إضافة عدد غير محدود من القنوات، ويجب أن يشترك المستخدم بكل قناة مفعّلة قبل دخول البوت.\n\n"
    if channels:
        text += "\n".join([f"• {c.title or c.chat_id}" for c in channels])
    else:
        text += "لا يوجد قنوات مضافة."

    await callback.message.edit_text(
        text,
        reply_markup=admin_channels_kb(channels),
    )


@router.callback_query(F.data == "admin:channel_add")
async def channel_add_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "➕ أرسل آيدي القناة أو يوزرها:\n"
        "(مثال: -1001234567890 أو @mychannel)\n\n"
        "⚠️ يجب أن يكون البوت أدمن بالقناة أولاً.",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminChannelStates.waiting_channel_id)


@router.message(AdminChannelStates.waiting_channel_id)
async def channel_add_received(
    message: Message,
    state: FSMContext,
    session,
    bot,
):
    raw = message.text.strip()
    try:
        chat = await bot.get_chat(raw)
    except Exception:
        await message.answer("⚠️ تعذر العثور على القناة.\nتأكد أن البوت أدمن فيها وأن المعرف صحيح.")
        return

    existing = await session.execute(
        select(MandatoryChannel).where(MandatoryChannel.chat_id == chat.id)
    )
    if existing.scalar_one_or_none():
        await message.answer("⚠️ هذه القناة مضافة مسبقاً.")
        await state.clear()
        return

    channel = MandatoryChannel(
        chat_id=chat.id,
        username_or_link=(f"https://t.me/{chat.username}" if chat.username else None),
        title=chat.title,
    )
    session.add(channel)
    await session.commit()

    await message.answer(f"✅ تمت إضافة القناة: {chat.title}")
    await state.clear()


@router.callback_query(F.data.startswith("admin:channel_del:"))
async def channel_delete(callback: CallbackQuery, session):
    channel_id = int(callback.data.split(":")[2])
    channel = await session.get(MandatoryChannel, channel_id)
    if channel:
        channel.is_active = False
        await session.commit()
        await callback.answer("✅ تم إلغاء تفعيل القناة.")
    else:
        await callback.answer("⚠️ غير موجود.", show_alert=True)
    await channels_list(callback, session)
