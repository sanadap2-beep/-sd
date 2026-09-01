"""
الإذاعة الجماعية.

إصلاحات:
1) إعادة المحاولة عند FloodControl/RetryAfter (429) بدل اعتبارها فشلاً
   نهائياً — كان البث يتوقف عن إرسال نصف الجمهور بمجرد تجاوز الحد.
2) جلب المستخدمين على دفعات بدل تحميلهم كلهم بالذاكرة مرة واحدة.
3) الرسائل النصية الأطول من 4096 حرف تُقسَّم تلقائياً، والكابشن الأطول
   من حد الصور (1024) يُقسَّم أيضاً — كان البث يفشل كاملاً بمثل هذه الرسالة.
4) المستخدم الذي حظر البوت أو لم يبدأه لا يُحسب "خطأ" بل يُتجاوز بصمت.
"""

import asyncio
import logging

from aiogram import Router, F
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from database.models import User, BroadcastLog
from states.states import AdminBroadcastStates
from keyboards.admin import admin_back_kb
from filters.admin_filter import IsAdmin

logger = logging.getLogger(__name__)

router = Router(name="admin_broadcast")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_TEXT_CHUNK = 4000
_CAPTION_CHUNK = 1000
_BATCH_SIZE = 500
_PROGRESS_EVERY = 25
_RETRY_AFTER_WAIT_EXTRA = 0.5


def _split_text(text: str, limit: int) -> list[str]:
    """يقسم نصاً طويلاً إلى أجزاء لا تتجاوز limit مع محاولة عدم كسر الكلمات."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def _send_with_retry(send_call, **kwargs) -> bool:
    """يرسل رسالة مع إعادة محاولة واحدة عند تجاوز حد Telegram (429)."""
    try:
        await send_call(**kwargs)
        return True
    except TelegramRetryAfter as exc:
        wait = exc.retry_after + _RETRY_AFTER_WAIT_EXTRA
        logger.info("FloodControl أثناء البث، انتظار %ss ثم إعادة المحاولة.", wait)
        await asyncio.sleep(wait)
        try:
            await send_call(**kwargs)
            return True
        except Exception:  # noqa: BLE001 - المحاولة الثانية فشلت فعلاً
            return False
    except TelegramForbiddenError:
        # المستخدم حظر البوت أو لم يبدأه — ليس خطأ حقيقياً.
        return True
    except Exception:  # noqa: BLE001 - أي فشل آخر يُعدّ فشلاً
        return False


async def _send_text_chunks(bot, telegram_id: int, text: str) -> bool:
    """يرسل نصاً مقسّماً بأمان؛ يعيد True إذا نجح كل شيء."""
    chunks = _split_text(text, _TEXT_CHUNK)
    if not chunks:
        chunks = [""]
    for chunk in chunks:
        if not await _send_with_retry(bot.send_message, chat_id=telegram_id, text=chunk):
            return False
    return True


async def _send_to_user(bot, telegram_id: int, message: Message) -> bool:
    """يرسل محتوى الرسالة (نص/صورة/فيديو/مستند) لأحد المستخدمين."""
    if message.photo:
        caption = message.caption or ""
        if len(caption) <= _CAPTION_CHUNK:
            return await _send_with_retry(
                bot.send_photo,
                chat_id=telegram_id,
                photo=message.photo[-1].file_id,
                caption=caption,
            )
        # كابشن أطول من حد الصور: نرسل الصورة بكابشن مختصر ثم بقية النص.
        short_caption = caption[:_CAPTION_CHUNK].rsplit("\n", 1)[0] or caption[:_CAPTION_CHUNK]
        if not await _send_with_retry(
            bot.send_photo,
            chat_id=telegram_id,
            photo=message.photo[-1].file_id,
            caption=short_caption,
        ):
            return False
        return await _send_text_chunks(bot, telegram_id, caption[_CAPTION_CHUNK:])

    if message.video:
        return await _send_with_retry(
            bot.send_video,
            chat_id=telegram_id,
            video=message.video[-1].file_id,
            caption=(message.caption or "")[:_CAPTION_CHUNK],
        )

    if message.document:
        return await _send_with_retry(
            bot.send_document,
            chat_id=telegram_id,
            document=message.document[-1].file_id,
            caption=(message.caption or "")[:_CAPTION_CHUNK],
        )

    return await _send_text_chunks(bot, telegram_id, message.text or message.caption or "")


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
    total = await session.scalar(
        select(func.count()).select_from(User).where(User.is_banned.is_(False))
    )

    progress_msg = await message.answer(
        f"📢 جاري الإرسال لـ {total or '؟'} مستخدم...\n⏳ يرجى الانتظار."
    )

    sent, failed, processed = 0, 0, 0

    offset = 0
    while True:
        ids_result = await session.execute(
            select(User.id)
            .where(User.is_banned.is_(False))
            .order_by(User.id)
            .limit(_BATCH_SIZE)
            .offset(offset)
        )
        user_ids = [row[0] for row in ids_result.all()]
        if not user_ids:
            break

        for telegram_id in user_ids:
            if await _send_to_user(bot, telegram_id, message):
                sent += 1
            else:
                failed += 1
            processed += 1

            if processed % _PROGRESS_EVERY == 0:
                try:
                    await progress_msg.edit_text(
                        f"📢 جاري الإرسال...\n\n"
                        f"📤 نجح: {sent}\n❌ فشل: {failed}\n"
                        f"⏳ تمت المعالجة: {processed}"
                    )
                except Exception:  # noqa: BLE001 - فشل تحرير التقدم لا يوقف البث
                    pass

        offset += _BATCH_SIZE

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
            f"✅ تم الإرسال.\n\n📤 نجح: {sent}\n❌ فشل: {failed}\n📊 الإجمالي: {total or processed}"
        )
    except Exception:  # noqa: BLE001
        await message.answer(f"✅ تم الإرسال.\n📤 نجح: {sent} | ❌ فشل: {failed}")

    await state.clear()
