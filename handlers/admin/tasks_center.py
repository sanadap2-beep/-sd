"""
مركز المهام — لوحة الأدمن.

الأدمن يتحكم بكل شيء من هنا بدون لمس الكود:
- إنشاء مهمة جديدة وتحديد نوعها وقيمة نقاطها.
- تعديل النقاط والحد اليومي وآلية التحقق لأي مهمة.
- تفعيل/إيقاف أي مهمة.
- مراجعة التقديمات التي تحتاج موافقة (ترجمة/إبلاغ عن مزود).

كل تعديل يُسجَّل في سجل التدقيق، وكل القيم تُقرأ من قاعدة البيانات
لا من الكود، فأي تغيير يسري فوراً.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.models import (
    Task,
    TaskRewardType,
    TaskSubmission,
    TaskType,
    TaskVerification,
)
from filters.admin_filter import IsAdmin
from keyboards.admin import admin_main_kb
from services.audit_service import AuditAction, AuditService
from services.task_service import TaskService
from sqlalchemy import select
from states.states import AdminTaskStates

router = Router(name="admin_tasks_center")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


_TYPE_LABELS = {
    TaskType.DAILY_CHECKIN: "📅 حضور يومي",
    TaskType.FIRST_DEPOSIT: "💳 أول شحن",
    TaskType.INVITE_FRIEND: "🤝 إحالة صديق",
    TaskType.REVIEW_PRODUCT: "⭐ تقييم منتج",
    TaskType.JOIN_CHANNEL: "📢 اشتراك قناة",
    TaskType.REPORT_PROVIDER: "🚨 إبلاغ عن مزود",
    TaskType.TRANSLATE_TEXT: "🌐 ترجمة نص",
    TaskType.WATCH_AD: "📺 مشاهدة إعلان",
    TaskType.PROFILE_COMPLETE: "👤 إكمال الملف",
    TaskType.CUSTOM: "🎯 مهمة مخصصة",
}

_VERIFICATION_LABELS = {
    TaskVerification.NONE: "بلا تحقق",
    TaskVerification.ONCE_PER_DAY: "مرة يومياً",
    TaskVerification.ONCE_PER_USER: "مرة واحدة فقط",
    TaskVerification.ADMIN_APPROVAL: "بموافقة أدمن",
    TaskVerification.TELEGRAM_MEMBERSHIP: "عضوية قناة",
    TaskVerification.PROOF_ORDER: "طلب مكتمل",
    TaskVerification.CAPTCHA: "تحدي captcha",
    TaskVerification.COOLDOWN_MINUTES: "مهلة بين المرات",
}


# ══════════════ القائمة ══════════════


@router.callback_query(F.data == "admin:tasks_center")
async def tasks_home(callback: CallbackQuery, session):
    result = await session.execute(select(Task).order_by(Task.sort_order, Task.id))
    tasks = list(result.scalars().all())
    rows = []
    for task in tasks:
        mark = "🟢" if task.is_active else "⚪"
        rows.append(
            [InlineKeyboardButton(text=f"{mark} {task.emoji} {task.title_ar}", callback_data=f"task_edit:{task.id}")]
        )
    rows.append([InlineKeyboardButton(text="➕ مهمة جديدة", callback_data="task_new")])
    rows.append([InlineKeyboardButton(text="📥 مراجعة التقديمات", callback_data="task_submissions")])
    rows.append([InlineKeyboardButton(text="🌱 استعادة المهام الافتراضية", callback_data="task_seed")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")])

    active = sum(1 for t in tasks if t.is_active)
    await callback.message.edit_text(
        "🎯 <b>مركز المهام مقابل النقاط</b>\n\n"
        f"المهام: <b>{len(tasks)}</b> | المفعّلة: <b>{active}</b>\n\n"
        "أنت تحدد المهمة وقيمة نقاطها وآلية التحقق،\n"
        "والبوت يتكفل بمنع التكرار والغش تلقائياً.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ══════════════ تفاصيل وتعديل ══════════════


@router.callback_query(F.data.startswith("task_edit:"))
async def task_detail(callback: CallbackQuery, session):
    task = await session.get(Task, int(callback.data.split(":")[1]))
    if task is None:
        await callback.answer("المهمة غير موجودة.", show_alert=True)
        return
    target = task.verification_target or "—"
    await callback.message.edit_text(
        f"{task.emoji} <b>{task.title_ar}</b>\n"
        f"<code>{task.key}</code>\n\n"
        f"النوع: {_TYPE_LABELS.get(task.task_type, task.task_type.value)}\n"
        f"المكافأة: <b>{task.reward_points} نقطة</b>"
        + (f" أو {task.reward_usd}$" if task.reward_usd else "")
        + "\n"
        f"التحقق: {_VERIFICATION_LABELS.get(task.verification, task.verification.value)}\n"
        f"قيمة التحقق: <code>{target}</code>\n"
        f"الحد اليومي: {task.daily_limit or 'بلا'} | الكلي: {task.total_limit or 'بلا'}\n"
        f"عمر الحساب الأدنى: {task.min_account_age_hours} ساعة\n"
        f"طلبات مطلوبة: {task.min_orders_required}\n"
        f"الحالة: {'🟢 نشطة' if task.is_active else '⚪ موقوفة'}",
        reply_markup=_task_kb(task),
    )
    await callback.answer()


def _task_kb(task: Task) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="⭐ النقاط", callback_data=f"task_points:{task.id}", style="primary")],
        [InlineKeyboardButton(text="🔁 التحقق", callback_data=f"task_verif:{task.id}")],
        [InlineKeyboardButton(text="📊 الحدود", callback_data=f"task_limits:{task.id}")],
        [
            InlineKeyboardButton(
                text="⚪ إيقاف" if task.is_active else "🟢 تفعيل",
                callback_data=f"task_toggle:{task.id}",
            )
        ],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:tasks_center")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("task_points:"))
async def task_points_ask(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split(":")[1])
    await state.set_state(AdminTaskStates.waiting_points)
    await state.update_data(task_id=task_id, field="reward_points")
    await callback.message.answer("⭐ أرسل قيمة النقاط الجديدة (أو «إلغاء»):")
    await callback.answer()


@router.callback_query(F.data.startswith("task_limits:"))
async def task_limits_ask(callback: CallbackQuery, state: FSMContext):
    task_id = int(callback.data.split(":")[1])
    await state.set_state(AdminTaskStates.waiting_limit)
    await state.update_data(task_id=task_id)
    await callback.message.answer(
        "📊 أرسل الحد اليومي ثم أرسل 0 لإلغاء الحد.\n"
        "الصيغة: <code>اليومي الكلي</code>\nمثال: <code>3 0</code>\n\n(أو «إلغاء»)"
    )
    await callback.answer()


@router.message(AdminTaskStates.waiting_limit)
async def task_limits_save(message: Message, state: FSMContext, session, db_user):
    data = await state.get_data()
    text = (message.text or "").strip()
    if text in ("إلغاء", "cancel"):
        await state.clear()
        return await message.answer("تم الإلغاء.")
    parts = text.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return await message.answer("⚠️ الصيغة: <code>اليومي الكلي</code> مثل <code>3 0</code>")
    daily, total = int(parts[0]), int(parts[1])
    task = await session.get(Task, data["task_id"])
    if task is None:
        await state.clear()
        return await message.answer("⚠️ المهمة غير موجودة.")
    old = f"{task.daily_limit} {task.total_limit}"
    task.daily_limit = daily
    task.total_limit = total
    await session.commit()
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="task",
        entity_id=task.id,
        entity_name=task.title_ar,
        old_value=old,
        new_value=f"{daily} {total}",
        description="تعديل حدود المهمة",
        session=session,
    )
    await state.clear()
    await message.answer(f"✅ حُفظ: يومي {daily} | كلي {total or 'بلا حد'}")


@router.message(AdminTaskStates.waiting_points, F.text.regexp(r"^\d+$"))
async def task_new_points(message: Message, state: FSMContext, session, db_user):
    data = await state.get_data()
    if data.get("field") != "new_task_points":
        return  # هذا المسار لتعديل مهمة موجودة، يعالجه المعالج الآخر
    points = int(message.text)
    if points <= 0:
        return await message.answer("⚠️ أرسل رقماً موجباً.")
    task_type = TaskType(data["task_type"])
    key = f"custom_{abs(hash(data['title'])) % 100000}_{task_type.value}"
    task = Task(
        key=key,
        title_ar=data["title"],
        emoji=_TYPE_LABELS.get(task_type, "🎯").split(" ")[0],
        task_type=task_type,
        reward_type=TaskRewardType.POINTS,
        reward_points=points,
        verification=TaskVerification.ONCE_PER_DAY,
        daily_limit=1,
        is_active=True,
    )
    session.add(task)
    await session.commit()
    await AuditService.log_create(
        admin_id=db_user.id,
        entity_type="task",
        entity_id=task.id,
        entity_name=task.title_ar,
        new_value=f"{task_type.value} / {points} نقطة",
        session=session,
    )
    await state.clear()
    await message.answer(
        f"✅ أُنشئت المهمة «{task.title_ar}» بمكافأة <b>{points} نقطة</b>.\n\n"
        "افتحها الآن لضبط آلية التحقق والحدود."
    )


@router.message(AdminTaskStates.waiting_points)
async def task_points_save(message: Message, state: FSMContext, session, db_user):
    data = await state.get_data()
    if data.get("field") == "new_task_points":
        return  # مسار إنشاء مهمة جديدة يعالجه معالج آخر
    text = (message.text or "").strip()
    if text in ("إلغاء", "cancel"):
        await state.clear()
        return await message.answer("تم الإلغاء.")
    if not text.isdigit() or int(text) <= 0:
        return await message.answer("⚠️ أرسل رقماً صحيحاً موجباً.")
    task = await session.get(Task, data["task_id"])
    if task is None:
        await state.clear()
        return await message.answer("⚠️ المهمة غير موجودة.")
    old = task.reward_points
    task.reward_points = int(text)
    await session.commit()
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="task",
        entity_id=task.id,
        entity_name=task.title_ar,
        old_value=str(old),
        new_value=str(int(text)),
        description="تعديل نقاط المهمة",
        session=session,
    )
    await state.clear()
    await message.answer(f"✅ صارت مكافأة «{task.title_ar}» = <b>{int(text)} نقطة</b>")


@router.callback_query(F.data.startswith("task_verif:"))
async def task_verification_menu(callback: CallbackQuery, session):
    task = await session.get(Task, int(callback.data.split(":")[1]))
    if task is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    rows = []
    for key, label in _VERIFICATION_LABELS.items():
        mark = "✅" if task.verification == key else "▫️"
        rows.append(
            [InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"task_setverif:{task.id}:{key.value}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data=f"task_edit:{task.id}")])
    await callback.message.edit_text(
        f"🔁 آلية التحقق لـ <b>{task.title_ar}</b>\n\n"
        "التحقق هو ما يمنع المستخدم من أخذ المكافأة بلا مجهود حقيقي.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task_setverif:"))
async def task_verification_set(callback: CallbackQuery, session, db_user):
    _, task_id, verification = callback.data.split(":", 2)
    task = await session.get(Task, int(task_id))
    if task is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    old = task.verification.value
    task.verification = TaskVerification(verification)
    await session.commit()
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="task",
        entity_id=task.id,
        entity_name=task.title_ar,
        old_value=old,
        new_value=verification,
        description="تغيير آلية تحقق المهمة",
        session=session,
    )
    await callback.answer("✅ تم تغيير آلية التحقق.")
    await task_detail(callback)


@router.callback_query(F.data.startswith("task_toggle:"))
async def task_toggle(callback: CallbackQuery, session, db_user):
    task = await session.get(Task, int(callback.data.split(":")[1]))
    if task is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    task.is_active = not task.is_active
    await session.commit()
    await AuditService.log_toggle(
        admin_id=db_user.id,
        entity_type="task",
        entity_id=task.id,
        entity_name=task.title_ar,
        new_status=task.is_active,
        session=session,
    )
    await callback.answer("🟢 مفعّلة" if task.is_active else "⚪ موقوفة")
    await task_detail(callback)


# ══════════════ مهمة جديدة ══════════════


@router.callback_query(F.data == "task_new")
async def task_new_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminTaskStates.waiting_title)
    await callback.message.answer(
        "➕ <b>مهمة جديدة</b>\n\nأرسل عنوان المهمة:\n(مثال: «شارك البوت مع 3 أصدقاء»)"
    )
    await callback.answer()


@router.message(AdminTaskStates.waiting_title)
async def task_new_title(message: Message, state: FSMContext):
    title = (message.text or "").strip()
    if not title or title in ("إلغاء", "cancel"):
        await state.clear()
        return await message.answer("تم الإلغاء.")
    await state.update_data(title=title[:128])
    await state.set_state(AdminTaskStates.waiting_type)
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"task_picktype:{key.value}")]
        for key, label in _TYPE_LABELS.items()
    ]
    await message.answer("اختر نوع المهمة:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("task_picktype:"))
async def task_new_type(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "title" not in data:
        await state.clear()
        return await callback.answer("انتهت الجلسة، ابدأ من جديد.", show_alert=True)
    task_type = callback.data.split(":", 1)[1]
    await state.update_data(task_type=task_type)
    await state.set_state(AdminTaskStates.waiting_points)
    await state.update_data(field="new_task_points")
    await callback.message.answer(f"⭐ كم نقطة تكافئ «{data['title']}»؟ أرسل رقماً:")
    await callback.answer()


# ══════════════ التقديمات ══════════════


@router.callback_query(F.data == "task_submissions")
async def submissions_list(callback: CallbackQuery, session):
    subs = await TaskService.pending_submissions(session, 10)
    if not subs:
        await callback.message.edit_text(
            "📥 لا تقديمات قيد المراجعة.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:tasks_center")]]
            ),
        )
        await callback.answer()
        return
    rows = []
    for sub in subs:
        rows.append(
            [InlineKeyboardButton(text=f"📄 تقديم #{sub.id}", callback_data=f"task_sub:{sub.id}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:tasks_center")])
    await callback.message.edit_text(
        f"📥 <b>تقديمات بانتظار مراجعتك</b> ({len(subs)})\n\n"
        "لا تُصرف أي مكافأة قبل موافقتك.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task_sub:"))
async def submission_view(callback: CallbackQuery, session):
    sub = await session.get(TaskSubmission, int(callback.data.split(":")[1]))
    if sub is None:
        await callback.answer("غير موجود.", show_alert=True)
        return
    user = await session.get(type(sub.user), sub.user_id)
    task = await session.get(Task, sub.task_id)
    rows = [
        [
            InlineKeyboardButton(text="✅ قبول وصرف المكافأة", callback_data=f"task_approve:{sub.id}"),
            InlineKeyboardButton(text="❌ رفض", callback_data=f"task_reject:{sub.id}", style="danger"),
        ],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="task_submissions")],
    ]
    await callback.message.edit_text(
        f"📄 <b>تقديم #{sub.id}</b>\n\n"
        f"المهمة: {task.title_ar if task else '—'}\n"
        f"المكافأة: {task.reward_points if task else 0} نقطة\n"
        f"المستخدم: <code>{sub.user_id}</code>"
        + (f" (@{user.username})" if user and user.username else "")
        + f"\nالتاريخ: {sub.created_at:%Y-%m-%d %H:%M}\n\n"
        f"<b>المحتوى:</b>\n{(sub.content or '—')[:1500]}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("task_approve:"))
async def submission_approve(callback: CallbackQuery, session, db_user, bot):
    sub_id = int(callback.data.split(":")[1])
    result = await TaskService.review_submission(session, sub_id, db_user.id, approve=True)
    if result is None:
        await callback.answer("سبق معالجته.", show_alert=True)
        return
    try:
        await bot.send_message(result.user.telegram_id, "✅ قُبل تقديمك وصُرفت مكافأتك.")
    except Exception:
        pass
    await callback.answer("✅ قُبل وصُرفت المكافأة.")
    await submissions_list(callback)


@router.callback_query(F.data.startswith("task_reject:"))
async def submission_reject(callback: CallbackQuery, session, db_user, bot):
    sub_id = int(callback.data.split(":")[1])
    result = await TaskService.review_submission(
        session, sub_id, db_user.id, approve=False, note="مرفوض"
    )
    if result is None:
        await callback.answer("سبق معالجته.", show_alert=True)
        return
    try:
        await bot.send_message(result.user.telegram_id, "❌ رُفض تقديمك.")
    except Exception:
        pass
    await callback.answer("❌ رُفض.")
    await submissions_list(callback)


@router.callback_query(F.data == "task_seed")
async def task_seed(callback: CallbackQuery, session):
    created = await TaskService.seed_defaults(session)
    await callback.answer(
        f"🌱 أُضيفت {created} مهمة افتراضية." if created else "ℹ️ كل المهام الافتراضية موجودة."
    )
