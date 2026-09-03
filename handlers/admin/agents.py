"""
💼 إدارة الوكلاء:
- إنشاء أكواد وكالة (تُستعمل مرة واحدة).
- عرض الوكلاء الحاليين + المسحوبين مع إحصاءات كاملة
  (إيداع أسبوعي، مشتريات، طلبات، إجمالي الإنفاق).
- أزرار: زيادة نسبة الخصم / تقليلها / إلغاء الوكالة.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User
from filters.admin_filter import IsAdmin
from keyboards.agent_admin import (
    agent_admin_back_kb,
    agent_admin_list_kb,
    agent_codes_kb,
    agent_detail_kb,
)
from services.agent_service import AgentError, AgentService
from services.feature_service import FeatureService
from states.states import AdminAgentStates

router = Router(name="admin_agents")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def _user_label(user: User | None, fallback_id: int) -> str:
    if user is None:
        return f"#{fallback_id}"
    name = user.full_name or f"@{user.username}" if user.username else f"#{user.telegram_id}"
    return f"{name} (<code>{user.telegram_id}</code>)"


async def _list_text(session) -> str:
    agents = await AgentService.active_agents(session)
    revoked = await AgentService.revoked_agents(session)
    codes = await AgentService.unused_codes(session)
    threshold = await FeatureService.config("agent_program", "min_weekly_deposit_usd", 20)
    text = (
        "💼 <b>إدارة الوكلاء</b>\n\n"
        f"🟢 وكلاء فعّلون: <b>{len(agents)}</b>\n"
        f"🔴 مسخوبة: <b>{len(revoked)}</b>\n"
        f"🔑 أكواد متاحة: <b>{len(codes)}</b>\n"
        f"📏 حد الإيداع الأسبوعي: <b>{threshold}$</b>"
        " (يُضبط من مركز الإضافات)"
    )
    return text


@router.callback_query(F.data == "admin:agents")
async def agents_list(callback: CallbackQuery, session):
    text = await _list_text(session)
    agents = await AgentService.active_agents(session)
    revoked = await AgentService.revoked_agents(session)
    await callback.message.edit_text(
        text,
        reply_markup=agent_admin_list_kb(agents, revoked),
        disable_web_page_preview=True,
    )
    await callback.answer()


@router.callback_query(F.data == "admin:agent_create")
async def agent_create_start(callback: CallbackQuery, state: FSMContext):
    default_percent = await FeatureService.config("agent_program", "default_percent", 10)
    await callback.message.edit_text(
        "🔑 <b>إنشاء كود وكالة</b>\n\n"
        f"أرسل نسبة الخصم للكود (رقم %).\n"
        f"الإرسال 0 = النسبة الافتراضية ({default_percent}%).\n\n"
        "ثم أرسل الكود للمستخدم ليُفعّل وكالته.",
        reply_markup=agent_admin_back_kb(),
    )
    await state.set_state(AdminAgentStates.waiting_code_percent)
    await callback.answer()


@router.message(AdminAgentStates.waiting_code_percent)
async def agent_create_percent_received(
    message: Message, state: FSMContext, session, db_user: User
):
    raw = (message.text or "").strip()
    try:
        percent = float(raw)
    except ValueError:
        await message.answer("⚠️ أرسل رقماً (مثال: 10).", reply_markup=agent_admin_back_kb())
        return
    from decimal import Decimal

    percent_value = Decimal(str(percent)) if percent > 0 else None
    code = await AgentService.create_code(session, db_user.id, percent_value)
    await state.clear()
    await message.answer(
        f"✅ <b>تم إنشاء الكود</b>\n\n"
        f"<code>{code.code}</code>\n\n"
        f"💼 نسبة الخصم: <b>{code.percent}%</b>\n"
        f"📋 انسخه وأرسله للمستخدم — يدخله من زر «💼 برنامج الوكلاء» في البوت."
    )


@router.callback_query(F.data == "admin:agent_codes")
async def agent_codes_list(callback: CallbackQuery, session):
    codes = await AgentService.unused_codes(session)
    lines = ["🔑 <b>أكواد غير مستعملة</b>", ""]
    if not codes:
        lines.append("لا توجد أكواد متاحة.")
    for code in codes:
        lines.append(f"• <code>{code.code}</code> — خصم {code.percent}%")
    await callback.message.edit_text("\n".join(lines), reply_markup=agent_codes_kb(codes))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:agent_void:"))
async def agent_code_void(callback: CallbackQuery, session):
    code_id = int(callback.data.split(":")[2])
    if await AgentService.void_code(session, code_id):
        await callback.answer("تم إلغاء الكود.")
    else:
        await callback.answer("الكود غير متاح للإلغاء.", show_alert=True)
    await agent_codes_list(callback, session)


async def _detail_text(session, user_id: int) -> str:
    profile = await AgentService.profile(session, user_id)
    if profile is None:
        raise AgentError("❌ لا يوجد وكيل بهذا المستخدم.")
    user = await session.get(User, user_id)
    stats = await AgentService.weekly_stats(session, user_id)
    threshold = await FeatureService.config("agent_program", "min_weekly_deposit_usd", 20)

    status_line = (
        "🟢 <b>وكيل فعّال</b>"
        if profile.status == "active"
        else f"🔴 <b>وكالة مسحوبة</b>\nسبب: {profile.revoke_reason or '—'}"
    )
    activated = profile.activated_at.strftime("%Y-%m-%d") if profile.activated_at else "—"
    ok = stats["week_deposits_usd"] >= threshold
    bar = "✅" if ok else "⚠️"

    return (
        f"💼 <b>تفاصيل الوكيل</b>\n\n"
        f"👤 {_user_label(user, user_id)}\n"
        f"{status_line}\n"
        f"💸 نسبة الخصم: <b>{profile.percent}%</b>\n"
        f"📅 منذ: {activated}\n\n"
        f"{bar} إيداع آخر 7 أيام: <b>{stats['week_deposits_usd']}$</b> (المطلوب {threshold}$)\n"
        f"🛒 مشتريات 7 أيام: {stats['week_purchases_usd']}$\n"
        f"📦 طلبات 7 أيام: {stats['week_orders']}\n"
        f"💰 إجمالي الإنفاق: {stats['total_spent_usd']}$"
    )


@router.callback_query(F.data.startswith("admin:agent_detail:"))
async def agent_detail(callback: CallbackQuery, session):
    user_id = int(callback.data.split(":")[2])
    profile = await AgentService.profile(session, user_id)
    if profile is None:
        await callback.answer("الوكيل غير موجود.", show_alert=True)
        return
    text = await _detail_text(session, user_id)
    await callback.message.edit_text(
        text, reply_markup=agent_detail_kb(profile)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:agent_pct:"))
async def agent_adjust_percent(callback: CallbackQuery, session):
    _, _, user_id_str, delta_str = callback.data.split(":")
    from decimal import Decimal

    profile = await AgentService.adjust_percent(
        session, int(user_id_str), Decimal(delta_str)
    )
    await callback.answer(f"النسبة الآن {profile.percent}%")
    text = await _detail_text(session, profile.user_id)
    await callback.message.edit_text(text, reply_markup=agent_detail_kb(profile))


@router.callback_query(F.data.startswith("admin:agent_revoke:"))
async def agent_revoke(callback: CallbackQuery, session, db_user: User):
    user_id = int(callback.data.split(":")[2])
    user = await session.get(User, user_id)
    if user is None or not user.full_name and not user.username:
        label = f"#{user_id}"
    else:
        label = user.full_name or user.username or str(user.telegram_id)
    try:
        profile = await AgentService.revoke(
            session, user_id, f"سحب يدوي من الأدمن #{db_user.telegram_id}", admin_id=db_user.id
        )
    except AgentError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    # إشعار الوكيل
    try:
        await callback.bot.send_message(
            user.telegram_id,
            "⚠️ <b>تمت إقالة وكالتك</b>\n\n"
            f"سبب: {profile.revoke_reason}\n"
            "للتفعيل مجدداً: تواصل مع الإدارة.",
        )
    except Exception:
        pass

    # إشعار الإدارة
    from services.notification_service import NotificationService

    notifier = NotificationService(callback.bot)
    await notifier.notify_admin(
        "🚨 <b>سُحبت وكالة يدوياً</b>\n\n"
        f"👤 المستخدم: {_user_label(user, user_id)}\n"
        f"سبب: {profile.revoke_reason}"
    )
    await callback.answer(f"تمت إقالة وكالة {label}")
    text = await _detail_text(session, user_id)
    await callback.message.edit_text(text, reply_markup=agent_detail_kb(profile))
