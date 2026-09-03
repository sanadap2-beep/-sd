"""
واجهة برنامج الوكلاء للمستخدم:
- زر رئيسي «💼 كن وكيلاً».
- إدخال الكود الذي يُسلَّم من الإدارة → وكيل فوري بخصم على كل شيء.
- عرض حالة الوكالة: النسبة، تقدم الإيداع الأسبوعي مقابل الحد.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import User
from keyboards.agent import agent_code_kb, agent_home_kb, agent_status_kb
from services.agent_service import AgentError, AgentService
from services.feature_service import FeatureService
from states.states import AgentStates

router = Router(name="agent")


def _lang(db_user) -> str:
    return getattr(db_user, "language_code", "ar") or "ar"


async def _how_it_works_text() -> str:
    default_percent = await FeatureService.config("agent_program", "default_percent", 10)
    threshold = await FeatureService.config("agent_program", "min_weekly_deposit_usd", 20)
    return (
        "💼 <b>كيف يعمل برنامج الوكلاء؟</b>\n\n"
        f"• تحصل على <b>خصم {default_percent}%</b> على <b>كل</b> المنتجات "
        "والخدمات (أرقام، ألعاب، رشق، تطبيقات، عروض...).\n"
        f"• الكود يُسلَّم لك من الإدارة مرة واحدة ويُستعمل مرة واحدة.\n"
        f"• <b>شرط البقاء:</b> إيداعاتك في البوت خلال كل أسبوع يجب أن تكون "
        f"≥ <b>{threshold}$</b>، وإلا تُسحب الوكالة تلقائياً."
        " (يمكن للإدارة إعادتها بكود جديد.)\n"
        "• نسبة الخصم قابلة للزيادة من الإدارة حسب نشاطك."
    )


@router.callback_query(F.data == "agent:home")
async def agent_home(callback: CallbackQuery, session, db_user: User):
    await callback.answer()
    is_agent = await AgentService.is_active_agent(session, db_user.id)
    if is_agent:
        text = "💼 <b>برنامج الوكلاء</b>\n\nأنت وكيل معتمد. عرض تفاصيل وكالتك وتقدمك الأسبوعي:"
    else:
        text = (
            "💼 <b>برنامج الوكلاء</b>\n\n"
            "احصل على خصم على كل المنتجات والخدمات.\n"
            "إذا تسلّمت كود وكالة من الإدارة أدخله هنا:"
        )
    await callback.message.edit_text(text, reply_markup=agent_home_kb(is_agent))


@router.callback_query(F.data == "agent:how")
async def agent_how(callback: CallbackQuery):
    text = await _how_it_works_text()
    await callback.message.edit_text(text, reply_markup=agent_home_kb(False))
    await callback.answer()


@router.callback_query(F.data == "agent:enter_code")
async def agent_enter_code_start(callback: CallbackQuery, state: FSMContext, session, db_user: User):
    if await AgentService.is_active_agent(session, db_user.id):
        await callback.answer("أنت وكيل بالفعل.", show_alert=True)
        return
    await state.set_state(AgentStates.waiting_code)
    await callback.message.edit_text(
        "🔑 <b>إدخال كود الوكالة</b>\n\n"
        "أرسل الكود الذي تسلّمته من الإدارة، مثال:\n"
        "<code>AGENT-XXXX-XXXX</code>",
        reply_markup=agent_code_kb(),
    )
    await callback.answer()


@router.message(AgentStates.waiting_code)
async def agent_code_received(message: Message, state: FSMContext, session, db_user: User):
    raw = (message.text or "").strip()
    if not raw:
        return
    try:
        profile = await AgentService.redeem_code(session, db_user.id, raw)
    except AgentError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    percent = profile.percent
    await message.answer(
        f"🎉 <b>مبروك! أصبحت وكلاء معتمداً</b>\n\n"
        f"💼 نسبة خصمك: <b>{percent}%</b> على كل المنتجات والخدمات.\n"
        f"⚠️ شرط البقاء: إيداع أسبوعي يكفي — تابع تقدمك من «💼 برنامج الوكلاء».\n\n"
        "خصمك يُطبَّق تلقائياً في كل عملية شراء (بما فيها الأرقام والعروض)."
    )


@router.callback_query(F.data == "agent:status")
async def agent_status(callback: CallbackQuery, session, db_user: User):
    profile = await AgentService.profile(session, db_user.id)
    if profile is None or profile.status != "active":
        await callback.answer("وكالتك غير فعّلة.", show_alert=True)
        await callback.message.edit_text(
            "💼 <b>برنامج الوكلاء</b>\n\nوكالتك غير فعّلة حالياً.",
            reply_markup=agent_home_kb(False),
        )
        return

    threshold = await FeatureService.config("agent_program", "min_weekly_deposit_usd", 20)
    stats = await AgentService.weekly_stats(session, db_user.id)
    deposits = stats["week_deposits_usd"]
    ok = deposits >= threshold
    bar_char = "✅" if ok else "⚠️"

    from datetime import datetime

    activated = profile.activated_at.strftime("%Y-%m-%d") if profile.activated_at else "—"

    text = (
        "💼 <b>تفاصيل وكالتي</b>\n\n"
        f"💸 نسبة خصمك: <b>{profile.percent}%</b>\n"
        f"📅 وكيل منذ: {activated}\n\n"
        f"{bar_char} <b>إيداع آخر 7 أيام:</b> <b>{deposits}$</b> من المطلوب {threshold}$\n"
        f"🛒 مشترياتك (7 أيام): {stats['week_purchases_usd']}$\n"
        f"📦 طلباتك (7 أيام): {stats['week_orders']}\n"
        f"💰 إجمالي إنفاقك: {stats['total_spent_usd']}$\n\n"
        + (
            "أنت في الأمان — واصل الشحن للحفاظ على الوكالة."
            if ok
            else f"⚠️ أكمل الإيداع حتى {threshold - deposits}$ خلال الأسبوع الحالي "
            "للاحتفاظ بالوكالة."
        )
    )
    await callback.message.edit_text(text, reply_markup=agent_status_kb())
    await callback.answer()
