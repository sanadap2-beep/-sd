"""
نقاطي — واجهة المستخدم.

يعرض للمستخدم رصيد نقاطه وقيمتها، ويسمح له باستبدالها رصيداً
أو الاحتفاظ بها للدفع المباشر عند الشراء (كل 100 نقطة = 1$ افتراضياً).
"""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database.models import User
from services.feature_service import FeatureService
from services.i18n_service import I18nService
from services.loyalty_service import LoyaltyError, LoyaltyService
from services.points_service import PointsService
from services.task_service import TaskService

router = Router(name="points")


def _lang(db_user) -> str:
    return getattr(db_user, "language_code", "ar") or "ar"


@router.callback_query(F.data == "points:home")
async def points_home(callback: CallbackQuery, session, db_user: User):
    if not await PointsService.enabled():
        await callback.answer("الدفع بالنقاط موقوف حالياً.", show_alert=True)
        return
    language = _lang(db_user)
    rate = await PointsService.points_per_usd()
    cap = await PointsService.max_payment_percent()
    points = db_user.loyalty_points or 0
    value = await PointsService.usd_for_points(points)
    tasks_on = await FeatureService.enabled("tasks_system")
    summary = await TaskService.user_summary(session, db_user.id)

    rows = [[InlineKeyboardButton(text="🎯 اكسب نقاطاً من المهام", callback_data="tasks:home")]]
    rows.append([InlineKeyboardButton(text="💱 استبدال النقاط رصيداً", callback_data="points_redeem")])
    rows.append([InlineKeyboardButton(text="⬅️", callback_data="menu:main")])

    await callback.message.edit_text(
        f"{I18nService.t('points_home_title', language)}\n\n"
        f"⭐ <b>رصيدك:</b> {points} نقطة\n"
        f"💵 <b>قيمتها:</b> {value}$\n"
        f"🔁 <b>سعر الصرف:</b> كل {rate} نقطة = 1$\n\n"
        f"🛒 <b>طريقتان للاستفادة:</b>\n"
        f"1) ادفع بها مباشرة عند أي شراء (حتى {cap}% من قيمة الطلب).\n"
        f"2) استبدلها رصيداً من الزر أدناه.\n\n"
        + (f"🏆 أنجزت {summary['tasks_completed']} مهمة وكسبت منها {summary['points_from_tasks']} نقطة."
           if tasks_on else ""),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "points_redeem")
async def points_redeem(callback: CallbackQuery, session, db_user: User):
    """استبدال كل النقاط المتاحة رصيداً بسعر الصرف الذي حدده الأدمن."""
    if not await PointsService.enabled():
        await callback.answer("الدفع بالنقاط موقوف حالياً.", show_alert=True)
        return
    points = db_user.loyalty_points or 0
    if points <= 0:
        await callback.answer("لا نقاط لديك بعد.", show_alert=True)
        return

    rate = await PointsService.points_per_usd()
    value = await PointsService.usd_for_points(points)
    if value <= 0:
        await callback.answer(f"تحتاج {rate} نقطة على الأقل.", show_alert=True)
        return

    # نستخدم مسار النقاط نفسه حتى يبقى سعر الصرف واحداً في كل البوت،
    # ثم نضيف القيمة رصيداً.
    from database.models import TransactionType
    from services.balance_service import BalanceService
    from services.points_service import PointsError

    try:
        spent_value = await PointsService.spend(
            session, db_user.id, points, "استبدال نقاط رصيداً"
        )
    except PointsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    await BalanceService.add_balance(
        session,
        db_user.id,
        spent_value,
        TransactionType.LOYALTY_REDEEM,
        description=f"استبدال {points} نقطة",
        payment_reference=f"points_redeem:{db_user.id}:{points}:{int(value * 10000)}",
    )
    await session.refresh(db_user)
    await callback.answer(f"💱 أُضيف {spent_value}$ إلى رصيدك.")
    await points_home(callback)
