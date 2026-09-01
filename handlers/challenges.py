"""واجهة تحديات المستخدم ومكافآتها."""

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from database.models import User
from keyboards.challenge import challenge_kb
from services.gamification_service import GamificationService

router = Router(name="challenges")


async def _render(target, session, user_id: int):
    rows = await GamificationService.user_progress(session, user_id)
    lines = ["🎯 <b>التحديات</b>\n", "أكمل التحديات واجمع نقاط ولاء إضافية:"]
    if not rows:
        lines.append("\nلا توجد تحديات فعالة حالياً.")
    for challenge, progress in rows:
        current = progress.progress if progress else 0
        state = (
            "✅ مكتمل" if progress and progress.completed else f"{current}/{challenge.target_value}"
        )
        lines.append(
            f"\n🎯 <b>{escape(challenge.title)}</b> · {state}\n"
            f"{escape(challenge.description)}\n"
            f"🏆 المكافأة: {challenge.reward_points} نقطة"
        )
    text = "\n".join(lines)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=challenge_kb())
    else:
        await target.answer(text, reply_markup=challenge_kb())


@router.callback_query(F.data == "menu:challenges")
async def challenges_page(callback: CallbackQuery, session, db_user: User):
    await callback.answer()
    await _render(callback, session, db_user.id)


@router.message(F.text == "🎯 التحديات")
async def challenges_message(message: Message, session, db_user: User):
    await _render(message, session, db_user.id)
