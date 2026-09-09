"""Anti-spam referral protection system.

من يدخل عبر رابط إحالة يبقى ``referral_check_pending`` حتى يجتاز فحصاً بشرياً
بسيطاً (اختيار الرقم الصحيح). عند النجاح يُفعَّل حسابُه وتُدفع مكافأة المحيل.
عند تكرار الفشل حتى الحد الأقصى يُحظر الحساب (ومحيله حسب الإعدادات).
"""
import logging
import random

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User
from keyboards.referral_guard import human_check_markup
from services.referral_guard_service import ReferralGuardService
from services.settings_service import SettingsService
from states.states import ReferralGuardStates

logger = logging.getLogger(__name__)
router = Router(name="referral_guard")

SETTING_MAX_FAILED = "referral_guard_max_failed"
SETTING_MATH_DIFFICULTY = "referral_guard_math_difficulty"


async def _maxf() -> int:
    return await SettingsService.get_int(SETTING_MAX_FAILED, 5)


async def _challenge_text(answer: int, fails: int = 0, max_fails: int = 5) -> str:
    remaining = max(0, max_fails - fails)
    lines = [
        "🛡 <b>التحقق البشري</b>\n",
        "عذراً، وصلت عبر رابط إحالة، ولحماية النظام من البوتات "
        "اضغط على الزر الذي يحمل الرقم:",
        f"\n<b>{answer}</b>\n",
    ]
    if fails > 0:
        lines.append(f"⚠️ إجابة خاطئة ({fails}/{max_fails}).\n")
    lines.append(
        "🧠 المحاولات المتبقية: <b>%d</b>" % remaining
        + "\n\nلا تُدفع مكافأة المحيل ولا يُفعَّل حسابك إلا بعد نجاح التحقق."
    )
    return "\n".join(lines)


async def send_human_check(message: Message, state: FSMContext, db_user: User) -> None:
    """يعرض اختبار الإحالة للمستخدم (يُستدعى من /start وأزرار الاشتراك).

    الصحيح مسجل في حالة المحادثة (ليس في بيانات الزر) حتى لا يستطيع
    البوت قراءته من النداء.
    """
    answer = random.randint(0, 9)
    await state.update_data(human_check_answer=answer)
    await state.set_state(ReferralGuardStates.waiting_answer)
    text = await _challenge_text(
        answer, db_user.referral_check_fails or 0, await _maxf()
    )
    await message.answer(text, reply_markup=human_check_markup(), parse_mode="HTML")


@router.callback_query(ReferralGuardStates.waiting_answer, F.data.startswith("rg:ans:"))
async def human_check_answer(
    callback: CallbackQuery, state: FSMContext, session, db_user: User, bot
):
    try:
        digit = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("⚠️ إجابة غير صالحة.", show_alert=True)
        return

    data = await state.get_data()
    correct = data.get("human_check_answer")
    max_fails = await _maxf()
    fails = db_user.referral_check_fails or 0

    if correct is not None and digit == int(correct):
        # ── نجاح: تفعيل + مكافأة المحيل ──
        await ReferralGuardService.complete(session, db_user)
        if not db_user.is_activated:
            db_user.is_activated = True
            await session.commit()
            await _pay_referral_bonus(session, db_user, bot)
        await state.clear()
        await callback.answer("✅ تم التحقق بنجاح!", show_alert=True)
        await callback.message.edit_text("✅ تم التحقق بنجاح! يمكنك الآن استخدام المتجر.")
        return

    # ── فشل: تسجيل محاولة ──
    fails = await ReferralGuardService.record_failure(session, db_user)
    if fails >= max_fails:
        await ReferralGuardService.apply_penalty(session, db_user, bot)
        await state.clear()
        await callback.answer(
            "🚫 تجاوزت الحد الأقصى للمحاولات. تم إيقاف حسابك.",
            show_alert=True,
        )
        await callback.message.edit_text("🚫 تم حظرك بسبب تكرار المحاولات الفاشلة.")
        return

    # ── إعادة عرض اختبار جديد ──
    new_answer = random.randint(0, 9)
    await state.update_data(human_check_answer=new_answer)
    text = await _challenge_text(new_answer, fails, max_fails)
    await callback.message.edit_text(text, reply_markup=human_check_markup(), parse_mode="HTML")
    await callback.answer("⚠️ إجابة خاطئة.", show_alert=True)


async def _pay_referral_bonus(session, user: User, bot) -> None:
    """دفع مكافأة الإحالة للمحيل (منطق مطابق لتسلسل /start العادي)."""
    from decimal import Decimal

    from database.models import TransactionType
    from services.balance_service import BalanceService
    from services.i18n_service import I18nService
    from services.notification_service import NotificationService

    if user.referrer_id is None or user.referral_bonus_paid:
        return
    referrer = await session.get(User, user.referrer_id)
    if referrer is None:
        return
    bonus_usd = await SettingsService.get_decimal("referral_bonus_usd", Decimal("0.015"))
    if bonus_usd <= 0:
        return
    await BalanceService.add_balance(
        session,
        referrer.id,
        bonus_usd,
        TransactionType.REFERRAL_BONUS,
        description=f"مكافأة إحالة عن المستخدم {user.telegram_id}",
    )
    user.referral_bonus_paid = True
    await session.commit()
    if bot is not None:
        try:
            notifier = NotificationService(bot)
            await notifier.notify_user(
                referrer.telegram_id,
                I18nService.t("referral_bonus_notification", referrer.language_code, amount=f"{bonus_usd}"),
            )
        except Exception:
            logger.exception("فشل إشعار مكافأة الإحالة")


# ── إعدادات أمان إضافية (تستهدف أزرار التحقق القديمة إن وُجدت) ──
# نُبقي معالجاً يلتقط أي ضغطة على أزرار الأرقام حتى خارج الحالة، فلا يسقط
# المستخدم في «أكمل التحقق» إلى الأبد.
@router.callback_query(F.data.startswith("rg:ans:"))
async def human_check_orphan(callback: CallbackQuery):
    await callback.answer("أرسل /start لإكمال التحقق البشري.", show_alert=True)
