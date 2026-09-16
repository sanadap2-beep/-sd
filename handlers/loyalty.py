"""برنامج الولاء والمكافآت للمستخدمين."""

from datetime import date

from aiogram import F, Router
from aiogram.types import CallbackQuery

from database.models import User
from keyboards.loyalty import loyalty_kb
from services.gamification_service import GamificationService
from services.loyalty_service import LoyaltyError, LoyaltyService
from services.i18n_service import I18nService

router = Router(name="loyalty")


def _progress_bar(progress: int, required: int, size: int = 10) -> str:
    if required <= 0:
        return "██████████"
    ratio = max(0, min(1, progress / required))
    filled = round(ratio * size)
    return "█" * filled + "░" * (size - filled)


async def _render_loyalty(target, session, db_user: User):
    summary = await LoyaltyService.get_summary(session, db_user.id)
    tier = summary["tier"]
    next_tier = summary["next_tier"]
    points = summary["points"]
    can_claim = summary["last_checkin_date"] != date.today()

    language = db_user.language_code
    if next_tier:
        progress_text = I18nService.t(
            "loyalty_progress",
            language,
            emoji=next_tier.emoji,
            tier=next_tier.name,
            bar=_progress_bar(summary["progress"], summary["required"]),
            current=summary["progress"],
            required=summary["required"],
        )
    else:
        progress_text = I18nService.t("loyalty_top_tier", language)

    text = I18nService.t(
        "loyalty_card",
        language,
        tier_emoji=tier.emoji,
        tier=tier.name,
        points=points,
        streak=summary["streak"],
        benefit=tier.benefit,
        multiplier=tier.points_multiplier,
        progress=progress_text,
    )
    min_redeem = await _min_redeem_points()
    markup = loyalty_kb(points, can_claim, min_redeem)
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


async def _min_redeem_points() -> int:
    from services.settings_service import SettingsService

    return await SettingsService.get_int("loyalty_min_redeem_points", 100)


@router.callback_query(F.data == "menu:loyalty")
async def loyalty_menu(
    callback: CallbackQuery,
    session,
    db_user: User,
):
    await callback.answer()
    await _render_loyalty(callback, session, db_user)


@router.callback_query(F.data == "loyalty:daily")
async def loyalty_daily(
    callback: CallbackQuery,
    session,
    db_user: User,
):
    points, streak, already_claimed = await LoyaltyService.claim_daily(session, db_user.id)
    if already_claimed:
        await callback.answer(
            "🎁 استلمت مكافأة اليوم مسبقاً.",
            show_alert=True,
        )
    else:
        await GamificationService.progress_event(session, db_user.id, "daily_checkin")
        try:
            from services.weekly_challenge_service import WeeklyChallengeService

            await WeeklyChallengeService.record_event(session, db_user.id, event="checkins")
        except Exception:
            pass
        await callback.answer(
            f"🎉 حصلت على {points} نقطة! السلسلة: {streak} يوم.",
            show_alert=True,
        )
    await _render_loyalty(callback, session, db_user)


@router.callback_query(F.data.startswith("loyalty:redeem:"))
async def loyalty_redeem(
    callback: CallbackQuery,
    session,
    db_user: User,
):
    try:
        points = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        await callback.answer("⚠️ عدد نقاط غير صالح.", show_alert=True)
        return

    try:
        amount = await LoyaltyService.redeem_points(session, db_user.id, points)
    except LoyaltyError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await callback.answer(
        f"✅ تم استبدال {points} نقطة وإضافة {amount}$ لرصيدك.",
        show_alert=True,
    )
    await _render_loyalty(callback, session, db_user)


@router.message(F.text == "🎁 الولاء والمكافآت")
async def loyalty_message(
    message,
    session,
    db_user: User,
):
    await _render_loyalty(message, session, db_user)
