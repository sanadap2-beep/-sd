"""Anti-spam referral protection system."""
import logging, random
from sqlalchemy import select, func
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from database.models import User, ReferralAbuseLog
from services.settings_service import SettingsService
from keyboards.common import check_subscription_kb

logger = logging.getLogger(__name__)
router = Router(name="referral_guard")

SETTING_MAX_FAILED = "referral_guard_max_failed"
SETTING_BAN_ENABLED = "referral_guard_enabled"
SETTING_MATH_DIFFICULTY = "referral_guard_math_difficulty"

class RGState(StatesGroup):
    wait_sub = State()
    wait_math = State()

async def _maxf(): return await SettingsService.get_int(SETTING_MAX_FAILED, 5)
async def _enab(): return await SettingsService.get_bool(SETTING_BAN_ENABLED, True)

async def _gen(diff="simple"):
    a, b = (random.randint(50,200), random.randint(20,100)) if diff=="hard" else (random.randint(1,20), random.randint(1,15))
    op = random.choice(["+","-"])
    if op=="-" and a<b: a,b=b,a
    return f"ما هو {a} {op} {b}؟", a+b if op=="+" else a-b

async def start_guard(cb: CallbackQuery, rid: int, state: FSMContext):
    await state.update_data(rid=rid)
    from services.channel_service import ChannelService
    ch = await ChannelService.get_required_channels()
    if ch:
        await cb.message.edit_text("📢 اشترك:", reply_markup=check_subscription_kb(ch, "ref_guard:sub_checked"))
        await state.set_state(RGState.wait_sub)
    else: await _send(cb, state)

async def _send(cb, state):
    d = await SettingsService.get_string(SETTING_MATH_DIFFICULTY, "simple")
    q1,a1=await _gen(d); q2,a2=await _gen(d)
    await state.update_data(a=a1,b=a2)
    t=f"🧮 تحقق:\n\n1️⃣ {q1}\n2️⃣ {q2}\n\nأرسل الإجابتين."
    if isinstance(cb, CallbackQuery): await cb.message.edit_text(t)
    else: await cb.edit_text(t)
    await state.set_state(RGState.wait_math)

@router.callback_query(F.data=="ref_guard:sub_checked")
async def sub_ok(cb: CallbackQuery, state: FSMContext):
    await cb.answer(); await _send(cb, state)

@router.message(RGState.wait_math)
async def check(msg: Message, state: FSMContext, session):
    d = await state.get_data()
    try:
        p=msg.text.strip().split(); x,y=int(p[0]),int(p[1])
    except: await msg.answer("⚠️ أرسل رقمين."); return
    if x==d.get("a") and y==d.get("b"):
        await msg.answer("✅ ناجح!"); rid=d.get("rid")
        if rid:
            from services.referral_service import ReferralService
            await ReferralService.apply_referral_bonus(session, rid, msg.from_user.id)
        await state.clear(); return
    rid=d.get("rid")
    if rid:
        session.add(ReferralAbuseLog(referrer_user_id=rid, joiner_telegram_id=msg.from_user.id))
        c=(await session.execute(select(func.count(ReferralAbuseLog.id)).where(ReferralAbuseLog.referrer_user_id==rid))).scalar() or 0
        if c>=await _maxf() and await _enab():
            u=await session.get(User, rid)
            if u: u.is_banned=True
        await session.commit()
    await msg.answer("❌ خطأ."); await state.clear()
