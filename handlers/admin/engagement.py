"""
لوحة الأدمن لإدارة الميزات التفاعلية.

manage جوائز العجلة، قواعد مكافآت الشحن، التحديات الأسبوعية،
وإعدادات سوق الأرقام المستعملة — كل ما لا يصلح للتحكم عبر
الإعدادات البسيطة في feature_detail (JSON/config).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from filters.admin_filter import IsAdmin
from keyboards.admin_features import back_to_features_kb

router = Router(name="admin_engagement")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════ إدارة جوائز عجلة الحظ ══════════════


@router.callback_query(F.data == "admin_engage:prizes")
async def admin_prizes_home(callback: CallbackQuery, session):
    from services.spin_service import SpinService

    prizes = await SpinService.all_prizes(session)
    lines = ["🎰 <b>إدارة جوائز عجلة الحظ</b>\n"]
    if prizes:
        total_weight = sum(p.weight for p in prizes)
        for p in prizes:
            mark = "🟢" if p.is_active else "⚪"
            prob = f"{(p.weight / total_weight * 100):.1f}%" if total_weight else "—"
            lines.append(f"{mark} {p.name_ar} — {p.prize_type} {p.value:g}$ — وزن {p.weight} ({prob})")
    else:
        lines.append("لا توجد جوائز. أضف جائزة أولى.")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ جائزة جديدة", callback_data="admin_engage:prize_new")],
            [InlineKeyboardButton(text="⬅️ رجوع", callback_data="feat_home")],
        ]
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


class PrizeFSM(StatesGroup):
    waiting_name = State()
    waiting_type = State()
    waiting_value = State()
    waiting_weight = State()


@router.callback_query(F.data == "admin_engage:prize_new")
async def admin_prize_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(PrizeFSM.waiting_name)
    await callback.message.answer(
        "أرسل اسم الجائزة (مثلاً: 1$ هدية):",
        reply_markup=back_to_features_kb("daily_spin"),
    )
    await callback.answer()


@router.message(PrizeFSM.waiting_name)
async def admin_prize_name(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text or text.lower() in {"إلغاء", "cancel"}:
        await state.clear()
        await message.answer("تم الإلغاء.")
        return
    await state.update_data(name=text[:128])
    await state.set_state(PrizeFSM.waiting_type)
    await message.answer(
        "نوع الجائزة:\n"
        "<code>balance</code> — رصيد\n"
        "<code>points</code> — نقاط ولاء\n"
        "<code>nothing</code> — لا شيء (حظ أوفر)"
    )


@router.message(PrizeFSM.waiting_type)
async def admin_prize_type(message: Message, state: FSMContext):
    text = (message.text or "").strip().lower()
    if text not in {"balance", "points", "nothing"}:
        await message.answer("أرسل: balance أو points أو nothing")
        return
    await state.update_data(prize_type=text)
    if text == "nothing":
        await state.update_data(value=0, weight=10)
        await state.set_state(PrizeFSM.waiting_weight)
        await message.answer("الوزن النسبي (كلما زاد ارتفعت الاحتمال):")
    else:
        await state.set_state(PrizeFSM.waiting_value)
        await message.answer("القيمة ($ للرصيد أو عدد النقاط):")


@router.message(PrizeFSM.waiting_value)
async def admin_prize_value(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        await message.answer("أرسل رقماً صالحاً.")
        return
    if value < 0:
        await message.answer("القيمة يجب أن تكون ≥ 0.")
        return
    await state.update_data(value=float(value))
    await state.set_state(PrizeFSM.waiting_weight)
    await message.answer("الوزن النسبي (كلما زاد ارتفعت الاحتمال):")


@router.message(PrizeFSM.waiting_weight)
async def admin_prize_save(message: Message, state: FSMContext, session, db_user):
    text = (message.text or "").strip()
    try:
        weight = int(text)
    except (ValueError, TypeError):
        await message.answer("أرسل عدداً صحيحاً.")
        return
    if weight <= 0:
        await message.answer("الوزن يجب أن يكون ≥ 1.")
        return
    data = await state.get_data()
    from services.spin_service import SpinService

    prize = await SpinService.add_prize(
        session,
        name_ar=data["name"],
        prize_type=data["prize_type"],
        value=Decimal(str(data.get("value", 0))),
        weight=weight,
    )
    await state.clear()
    await message.answer(
        f"✅ تمت إضافة الجائزة: <b>{prize.name_ar}</b> (وزن {prize.weight})",
        reply_markup=back_to_features_kb("daily_spin"),
    )


@router.callback_query(F.data.startswith("admin_engage:prize_toggle:"))
async def admin_prize_toggle(callback: CallbackQuery, session):
    prize_id = int(callback.data.split(":")[3])
    from services.spin_service import SpinService

    await SpinService.toggle_prize(session, prize_id)
    await callback.answer("تم التبديل.")
    await admin_prizes_home(callback, session)


@router.callback_query(F.data.startswith("admin_engage:prize_del:"))
async def admin_prize_delete(callback: CallbackQuery, session):
    prize_id = int(callback.data.split(":")[3])
    from services.spin_service import SpinService

    ok = await SpinService.delete_prize(session, prize_id)
    await callback.answer("✅ تم الحذف." if ok else "غير موجود.")
    await admin_prizes_home(callback, session)


# ══════════════ إدارة قواعد مكافآت الشحن ══════════════


@router.callback_query(F.data == "admin_engage:deposit_rules")
async def admin_deposit_rules_home(callback: CallbackQuery, session):
    from services.deposit_bonus_service import DepositBonusService

    rules = await DepositBonusService.all_rules(session)
    lines = ["🎁 <b>قواعد مكافآت الشحن</b>\n"]
    if rules:
        for r in rules:
            mark = "🟢" if r.is_active else "⚪"
            lines.append(
                f"{mark} إيداع ≥ {r.min_deposit_usd:g}$ → نسبة {r.bonus_percent:g}%"
                f" (حد أقصى {r.max_bonus_usd:g}$)"
            )
    else:
        lines.append("لا قواعد حالياً.")
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ قاعدة جديدة", callback_data="admin_engage:deposit_new")],
            [InlineKeyboardButton(text="⬅️ رجوع", callback_data="feat_home")],
        ]
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


class DepositRuleFSM(StatesGroup):
    waiting_min = State()
    waiting_percent = State()
    waiting_max = State()


@router.callback_query(F.data == "admin_engage:deposit_new")
async def admin_deposit_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(DepositRuleFSM.waiting_min)
    await callback.message.answer("الحد الأدنى للإيداع بالدولار:")
    await callback.answer()


@router.message(DepositRuleFSM.waiting_min)
async def admin_deposit_min(message: Message, state: FSMContext):
    try:
        val = Decimal((message.text or "").strip())
    except (InvalidOperation, ValueError):
        await message.answer("أرسل رقماً صالحاً.")
        return
    if val <= 0:
        await message.answer("الحد الأدنى يجب أن يكون أكبر من صفر.")
        return
    await state.update_data(min_deposit=float(val))
    await state.set_state(DepositRuleFSM.waiting_percent)
    await message.answer("النسبة المئوية للمكافأة (مثلاً: 2 للحصول على 2%):")


@router.message(DepositRuleFSM.waiting_percent)
async def admin_deposit_percent(message: Message, state: FSMContext):
    try:
        val = Decimal((message.text or "").strip())
    except (InvalidOperation, ValueError):
        await message.answer("أرسل رقماً صالحاً.")
        return
    if val <= 0 or val > 50:
        await message.answer("النسبة يجب أن تكون بين 1 و 50.")
        return
    await state.update_data(percent=float(val))
    await state.set_state(DepositRuleFSM.waiting_max)
    await message.answer("المكافأة القصوى بالدولار (0 = بلا حد):")


@router.message(DepositRuleFSM.waiting_max)
async def admin_deposit_save(message: Message, state: FSMContext, session, db_user):
    try:
        max_val = Decimal((message.text or "").strip())
    except (InvalidOperation, ValueError):
        await message.answer("أرسل رقماً صالحاً.")
        return
    if max_val < 0:
        await message.answer("أرسل 0 أو رقماً موجباً.")
        return
    data = await state.get_data()
    from services.deposit_bonus_service import DepositBonusService

    await DepositBonusService.add_rule(
        session,
        Decimal(str(data["min_deposit"])),
        Decimal(str(data["percent"])),
        max_val,
    )
    await state.clear()
    await message.answer(
        "✅ تمت إضافة القاعدة.",
        reply_markup=back_to_features_kb("deposit_bonuses"),
    )


# ══════════════ إنشاء تحدي أسبوعي ══════════════


class ChallengeFSM(StatesGroup):
    waiting_title = State()
    waiting_desc = State()
    waiting_metric = State()
    waiting_target = State()
    waiting_reward = State()
    waiting_points = State()


@router.callback_query(F.data == "admin_engage:new_challenge")
async def admin_new_challenge(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ChallengeFSM.waiting_title)
    await callback.message.answer("عنوان التحدي (مثلاً: أكمل 5 طلبات):")
    await callback.answer()


@router.message(ChallengeFSM.waiting_title)
async def admin_challenge_title(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("أرسل عنواناً.")
        return
    await state.update_data(title=text[:128])
    await state.set_state(ChallengeFSM.waiting_desc)
    await message.answer("وصف مختصر للتحدي:")


@router.message(ChallengeFSM.waiting_desc)
async def admin_challenge_desc(message: Message, state: FSMContext):
    await state.update_data(desc=(message.text or "")[:500])
    await state.set_state(ChallengeFSM.waiting_metric)
    await message.answer(
        "المقياس:\n"
        "<code>orders</code> — عدد الطلبات\n"
        "<code>spend_usd</code> — مبلغ الإنفاق\n"
        "<code>checkins</code> — تسجيلات يومية\n"
        "<code>referrals</code> — إحالات"
    )


@router.message(ChallengeFSM.waiting_metric)
async def admin_challenge_metric(message: Message, state: FSMContext):
    metric = (message.text or "").strip().lower()
    if metric not in {"orders", "spend_usd", "checkins", "referrals"}:
        await message.answer("أرسل orders أو spend_usd أو checkins أو referrals")
        return
    await state.update_data(metric=metric)
    await state.set_state(ChallengeFSM.waiting_target)
    await message.answer("الهدف (مثلاً: 5 للطلبات، 50 للإنفاق):")


@router.message(ChallengeFSM.waiting_target)
async def admin_challenge_target(message: Message, state: FSMContext):
    try:
        val = Decimal((message.text or "").strip())
    except (InvalidOperation, ValueError):
        await message.answer("أرقام فقط.")
        return
    if val <= 0:
        await message.answer("الهدف يجب أن يكون أكبر من صفر.")
        return
    await state.update_data(target=float(val))
    await state.set_state(ChallengeFSM.waiting_reward)
    await message.answer("مكافأة الرصيد بالدولار (0 بدون رصيد):")


@router.message(ChallengeFSM.waiting_reward)
async def admin_challenge_reward(message: Message, state: FSMContext):
    try:
        val = Decimal((message.text or "").strip())
    except (InvalidOperation, ValueError):
        await message.answer("أرقام فقط.")
        return
    if val < 0:
        await message.answer("أرسل 0 أو رقماً موجباً.")
        return
    await state.update_data(reward=float(val))
    await state.set_state(ChallengeFSM.waiting_points)
    await message.answer("نقاط ولاء إضافية (0 بدون نقاط):")


@router.message(ChallengeFSM.waiting_points)
async def admin_challenge_save(message: Message, state: FSMContext, session, db_user):
    try:
        points = int((message.text or "").strip())
    except (ValueError, TypeError):
        await message.answer("أرسل عدداً صحيحاً.")
        return
    if points < 0:
        await message.answer("أرسل 0 أو عدداً موجباً.")
        return
    data = await state.get_data()
    from services.weekly_challenge_service import WeeklyChallengeService

    await WeeklyChallengeService.create_challenge(
        session,
        title=data["title"],
        description=data["desc"],
        emoji="🏆",
        metric=data["metric"],
        target_value=Decimal(str(data["target"])),
        reward_usd=Decimal(str(data["reward"])),
        reward_points=points,
    )
    await state.clear()
    await message.answer(
        "✅ تم إنشاء التحدي الأسبوعي بنجاح!",
        reply_markup=back_to_features_kb("weekly_challenges"),
    )


# ══════════════ إعدادات سوق الأرقام المستعملة ══════════════


@router.callback_query(F.data == "admin_engage:resale_settings")
async def admin_resale_settings(callback: CallbackQuery, session):
    from services.resale_service import ResaleService

    commission = await ResaleService.commission_percent(session)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎟 نسبة العمولة الحالية: {commission:g}%",
                    callback_data="feat_opt:number_resale_market:commission_percent",
                )
            ],
            [InlineKeyboardButton(text="⬅️ رجوع", callback_data="feat_item:number_resale_market")],
        ]
    )
    await callback.message.edit_text(
        "🔄 <b>إعدادات سوق الأرقام المستعملة</b>\n\n"
        f"نسبة العمولة الحالية: <b>{commission:g}%</b>\n\n"
        "لتعديل العمولة، استخدم زر الإعدادات في صفحة الإضافة.",
        reply_markup=kb,
    )
    await callback.answer()
