"""التحقق البشري للمستخدمين القادمين عبر روابط الإحالة (حماية من البوتات).

التدفق:
- يدخل المستخدم عبر ``/start ref_123`` → يُنشأ بـ referral_check_pending=True.
- أي تفاعل قبله غير /start وأزرار هذا الاختبار محجوب من UserMiddleware.
- /start (أو تأكيد الاشتراك) يعرض التحدي بدل تفعيل الحساب ودفع المكافأة.
- نجاح → تفعيل + مكافأة المحيل + إشعار للمحيل بقدوم المحال.
- فشل حتى الحد → يعتبر روبوتاً: حظر المحال + عقوبة المحيل + إشعار الأدمن.
"""

from __future__ import annotations

import logging
import random

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User
from keyboards.referral_guard import human_check_markup
from services.referral_guard_service import ReferralGuardService

logger = logging.getLogger(__name__)

router = Router(name="referral_guard")


async def send_human_check(message: Message, state: FSMContext, db_user: User) -> None:
    """يرسل تحدي التحقق البشري (الرقم الصحيح في حالة المحادثة فقط)."""
    answer = random.randint(0, 9)
    await state.update_data(rg_answer=answer)
    max_fails = await ReferralGuardService.max_fails()
    text = ReferralGuardService.challenge_text(
        answer,
        fails=db_user.referral_check_fails or 0,
        max_fails=max_fails,
    )
    await message.answer(text, reply_markup=human_check_markup())


@router.callback_query(F.data.startswith("rg:ans:"))
async def rg_answer_received(callback: CallbackQuery, session, db_user: User, state: FSMContext, bot):
    parts = callback.data.split(":")
    if len(parts) < 3 or not db_user.referral_check_pending:
        await callback.answer("جلسة التحقق منتهية — أرسل /start من جديد", show_alert=True)
        await state.clear()
        return
    try:
        chosen = int(parts[2])
    except ValueError:
        await callback.answer("زر غير صالح", show_alert=True)
        return

    data = await state.get_data()
    correct = data.get("rg_answer")
    if correct is None:
        # فُقدت حالة المحادثة (إعادة تشغيل للبوت) → سؤال جديد فوراً.
        await callback.answer()
        await send_human_check(callback.message, state, db_user)
        return

    if chosen != correct:
        fails = await ReferralGuardService.record_failure(session, db_user)
        max_fails = await ReferralGuardService.max_fails()
        await callback.answer("❌ إجابة خاطئة", show_alert=True)
        if fails >= max_fails:
            await callback.message.edit_text(
                "⛔️ تم تسجيل محاولات خاطئة متكررة.\n"
                "جارٍ تطبيق إجراءات الحماية..."
            )
            outcome = await ReferralGuardService.apply_penalty(session, db_user, bot=bot)
            lines = [
                "🤖 <b>تم اعتبار الحساب روبوتاً</b>\n",
                f"🆔 الحساب: <code>{db_user.telegram_id}</code>",
                f"🚫 حظر الحساب: {'نعم' if outcome['joiner_banned'] else 'لا'}",
                f"🚫 حظر صاحب رابط الإحالة: "
                f"{'نعم' if outcome['referrer_banned'] else 'لا'}",
                "\nتواصل مع الدعم إن كان هذا خطأً.",
            ]
            await callback.message.answer("\n".join(lines))
            await state.clear()
            return
        # إجابة خاطئة لكن المحاولات باقية → تحدٍّ جديد برقم جديد.
        try:
            await callback.message.edit_text(
                f"❌ إجابة خاطئة ({fails}/{max_fails}) — جرّب التحدي الجديد:"
            )
        except Exception:
            pass
        await send_human_check(callback.message, state, db_user)
        return

    # ── نجاح التحقق: تفعيل الحساب ثم مكافأة المحيل ──
    await ReferralGuardService.complete(session, db_user)
    db_user.is_activated = True
    await session.commit()
    await callback.answer("✅ تم التحقق بنجاح!")

    try:
        await callback.message.edit_text(
            f"✅ <b>تم التحقق بنجاح!</b>\n"
            "أهلاً بك في البوت 🎉"
        )
    except Exception:
        pass

    # إشعار المحيل بقدوم محال حقيقي (كان مؤجلاً حتى اجتياز الفحص).
    referrer: User | None = None
    if db_user.referrer_id is not None:
        referrer = await session.get(User, db_user.referrer_id)
    if referrer is not None:
        try:
            from services.notification_service import NotificationService

            notifier = NotificationService(bot)
            await notifier.notify_referrer_new_join(
                referrer.telegram_id,
                referrer.language_code,
                db_user.telegram_id,
                db_user.username,
                db_user.full_name,
            )
        except Exception:
            logger.exception("فشل إشعار المحيل بقدوم محال موثّق")

    # مكافأة الإحالة (لا تُدفع إلا بعد التحقق والتفعيل).
    try:
        from handlers.start import _try_pay_referral_bonus

        await _try_pay_referral_bonus(session, db_user, bot)
    except Exception:
        logger.exception("فشل دفع مكافأة الإحالة بعد تحقق بشري")

    # القائمة الرئيسية.
    try:
        from handlers.start import _build_menu, _main_header

        menu_kb = await _build_menu(session, db_user)
        header = await _main_header(session, db_user)
        await callback.message.answer(header, reply_markup=menu_kb)
    except Exception:
        logger.exception("تعذر عرض القائمة بعد التحقق")
        try:
            from keyboards.main_menu import build_main_menu

            await callback.message.answer(
                "✅ تم التحقق — استخدم /start لعرض القائمة",
                reply_markup=build_main_menu(
                    number_services=[],
                    categories=[],
                    balance_usd=f"{db_user.balance:.2f}",
                    language=db_user.language_code or "ar",
                    balance_display=f"{db_user.balance:.2f}$",
                ),
            )
        except Exception:
            await callback.message.answer("✅ تم التحقق — استخدم /start")
    await state.clear()
