"""
المهام مقابل النقاط — واجهة المستخدم.

المستخدم يرى المهام التي حددها الأدمن، ينجزها، ويكسب نقاطاً.
كل قواعد التحقق والحدود تُقرأ من قاعدة البيانات، فأي تعديل في
لوحة الأدمن ينعكس هنا فوراً بدون تغيير كود.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.models import Task, TaskType, TaskVerification
from services.feature_service import FeatureService
from services.i18n_service import I18nService
from services.points_service import PointsService
from services.task_service import TaskError, TaskService
from states.states import TaskUserStates

router = Router(name="tasks")


def _lang(db_user) -> str:
    return getattr(db_user, "language_code", "ar") or "ar"


@router.callback_query(F.data == "tasks:home")
async def tasks_home(callback: CallbackQuery, session, db_user):
    if not await FeatureService.enabled("tasks_system"):
        await callback.answer("نظام المهام موقوف حالياً.", show_alert=True)
        return
    language = _lang(db_user)
    tasks = await TaskService.active_tasks(session)
    summary = await TaskService.user_summary(session, db_user.id)
    rate = await PointsService.points_per_usd()

    rows = []
    for task in tasks:
        ok, reason = await TaskService.eligibility(session, db_user.id, task, callback.bot)
        mark = "✅" if ok else "🔒"
        rows.append([
            InlineKeyboardButton(
                text=f"{mark} {task.emoji} {task.title_ar[:24]} · ⭐{task.reward_points}",
                callback_data=f"task_do:{task.id}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⭐ نقاطي", callback_data="points:home")])
    rows.append([InlineKeyboardButton(text="⬅️", callback_data="menu:main")])

    await callback.message.edit_text(
        f"{I18nService.t('tasks_home_title', language)}\n\n"
        f"{I18nService.t('tasks_home_desc', language)}\n\n"
        f"🏆 أنجزت: <b>{summary['tasks_completed']}</b> مهمة\n"
        f"⭐ كسبت منها: <b>{summary['points_from_tasks']}</b> نقطة\n"
        f"💵 كل {rate} نقطة = 1$\n\n"
        "✅ متاحة الآن · 🔒 لم تُفتح بعد",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task_do:"))
async def task_do(callback: CallbackQuery, state: FSMContext, session, db_user, bot):
    task = await session.get(Task, int(callback.data.split(":")[1]))
    if task is None or not task.is_active:
        await callback.answer("المهمة غير متاحة.", show_alert=True)
        return

    # مهام تحتاج إدخالاً من المستخدم
    if task.task_type in (TaskType.REPORT_PROVIDER, TaskType.TRANSLATE_TEXT) or (
        task.verification == TaskVerification.ADMIN_APPROVAL
    ):
        await callback.answer()
        await callback.message.answer(
            f"{task.emoji} <b>{task.title_ar}</b>\n\n"
            f"{task.description_ar or 'اكتب ما تريد تقديمه، وسيراجعه الأدمن قبل صرف المكافأة.'}\n\n"
            "أرسل محتواك الآن:",
        )
        await state.set_state(TaskUserStates.waiting_content)
        await state.update_data(task_id=task.id)
        return

    if task.verification == TaskVerification.CAPTCHA:
        import random

        left, right = random.randint(3, 12), random.randint(3, 12)
        await state.set_state(TaskUserStates.waiting_captcha)
        await state.update_data(task_id=task.id, captcha_answer=left + right)
        await callback.answer()
        await callback.message.answer(
            f"{task.emoji} <b>{task.title_ar}</b>\n\n"
            f"🔢 أثبت أنك لست روبوتاً: كم ناتج <b>{left} + {right}</b>؟"
        )
        return

    try:
        result = await TaskService.complete(session, db_user.id, task, bot=bot)
    except TaskError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    language = _lang(db_user)
    earned = result["points"] or f"{result['usd']}$"
    await callback.answer(f"✅ {earned}")
    await callback.message.answer(I18nService.t("task_done", language, points=earned))
    await tasks_home(callback, session, db_user)


@router.message(TaskUserStates.waiting_content)
async def task_content(message: Message, state: FSMContext, session, db_user):
    data = await state.get_data()
    task_id = data.get("task_id")
    task = await session.get(Task, task_id) if task_id else None
    text = (message.text or "").strip()
    if task is None or not text:
        await state.clear()
        return await message.answer("⚠️ انتهت الجلسة، أعد المحاولة.")
    try:
        submission = await TaskService.submit(session, db_user.id, task, text)
    except TaskError as exc:
        await state.clear()
        return await message.answer(f"⚠️ {exc}")
    await state.clear()
    await message.answer(
        f"📥 <b>استلمنا تقديمك</b> (#{submission.id})\n\n"
        f"سيراجعه الأدمن، وعند القبول تُصرف لك <b>{task.reward_points} نقطة</b>."
    )


@router.message(TaskUserStates.waiting_captcha)
async def task_captcha(message: Message, state: FSMContext, session, db_user, bot):
    data = await state.get_data()
    text = (message.text or "").strip()
    if text in ("إلغاء", "cancel"):
        await state.clear()
        return await message.answer("تم الإلغاء.")
    try:
        answer = int(text)
    except ValueError:
        return await message.answer("⚠️ أرسل رقماً فقط.")
    if answer != int(data.get("captcha_answer", -1)):
        await FeatureService.track("tasks_system", "captcha_failed", user_id=db_user.id)
        await state.clear()
        return await message.answer("❌ إجابة خاطئة. أعد المحاولة من قائمة المهام.")

    task = await session.get(Task, data.get("task_id"))
    if task is None:
        await state.clear()
        return await message.answer("⚠️ المهمة غير متاحة.")
    await state.clear()
    try:
        result = await TaskService.complete(session, db_user.id, task, bot=bot)
    except TaskError as exc:
        return await message.answer(f"⚠️ {exc}")
    earned = result["points"] or f"{result['usd']}$"
    await message.answer(I18nService.t("task_done", _lang(db_user), points=earned))
