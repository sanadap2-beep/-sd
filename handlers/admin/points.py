"""
إدارة اقتصاد النقاط وعمولة التحويل — لوحة الأدمن.

يتحكم الأدمن هنا في:
- سعر صرف النقاط (كم نقطة = 1$).
- أقصى نسبة من قيمة الطلب تُدفع بالنقاط.
- عمولة التحويل بين المستخدمين وحدوده.
- منح/سحب نقاط من مستخدم محدد.

كل القيم تُقرأ من FeatureService فتسري فوراً على البوت.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.models import User
from filters.admin_filter import IsAdmin
from services.audit_service import AuditAction, AuditService
from services.balance_service import BalanceService
from services.feature_service import FeatureService
from services.loyalty_service import LoyaltyService
from services.points_service import PointsService
from sqlalchemy import func, select
from states.states import AdminPointsStates

router = Router(name="admin_points")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:points")
async def points_home(callback: CallbackQuery, session):
    rate = await PointsService.points_per_usd()
    cap = await PointsService.max_payment_percent()
    fee = await FeatureService.config_decimal("transfer_fee", "fee_percent", 1.0)
    min_amount = await FeatureService.config_decimal("transfer_fee", "min_amount_usd", 1.0)
    max_amount = await FeatureService.config_decimal("transfer_fee", "max_amount_usd", 1000.0)
    points_on = await FeatureService.enabled("points_currency")
    transfer_on = await FeatureService.enabled("transfer_fee")
    daily_tasks = await FeatureService.config_int("tasks_system", "daily_task_limit", 10)

    total_points = (
        await session.execute(select(func.coalesce(func.sum(User.loyalty_points), 0)))
    ).scalar_one()

    rows = [
        [InlineKeyboardButton(text=f"🔁 سعر الصرف: كل {rate} نقطة = 1$", callback_data="pts_rate")],
        [InlineKeyboardButton(text=f"📊 أقصى دفع بالنقاط: {cap}%", callback_data="pts_cap")],
        [InlineKeyboardButton(text=f"💸 عمولة التحويل: {fee}%", callback_data="pts_fee")],
        [InlineKeyboardButton(text=f"↔️ حدود التحويل: {min_amount}$–{max_amount}$", callback_data="pts_limits")],
        [InlineKeyboardButton(text=f"🎯 حد المهام اليومي: {daily_tasks}", callback_data="pts_taskcap")],
        [InlineKeyboardButton(text="🎁 منح/سحب نقاط مستخدم", callback_data="pts_grant")],
    ]
    await callback.message.edit_text(
        "⭐ <b>اقتصاد النقاط والتحويل</b>\n\n"
        f"الدفع بالنقاط: {'🟢 مفعّل' if points_on else '⚪ موقوف'}\n"
        f"عمولة التحويل: {'🟢 مفعّلة' if transfer_on else '⚪ موقوفة'}\n\n"
        f"مجموع النقاط المتداولة بين المستخدمين: <b>{int(total_points or 0)}</b>\n"
        f"قيمتها بالدولار: <b>{(int(total_points or 0) / rate):.2f}$</b>\n\n"
        "أي تعديل هنا يسري فوراً.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=rows + [[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")]]
        ),
    )
    await callback.answer()


# ══════════════ سعر الصرف ══════════════


@router.callback_query(F.data == "pts_rate")
async def ask_rate(callback: CallbackQuery, state: FSMContext):
    current = await PointsService.points_per_usd()
    await state.set_state(AdminPointsStates.waiting_rate)
    await callback.message.answer(
        f"🔁 كم نقطة تساوي دولاراً واحداً؟\n\nالحالي: <b>{current}</b>\n"
        "أرسل رقماً صحيحاً (مثال: <code>100</code>) أو «إلغاء»:"
    )
    await callback.answer()


@router.message(AdminPointsStates.waiting_rate)
async def save_rate(message: Message, state: FSMContext, session, db_user):
    text = (message.text or "").strip()
    if text in ("إلغاء", "cancel"):
        await state.clear()
        return await message.answer("تم الإلغاء.")
    if not text.isdigit() or int(text) <= 0:
        return await message.answer("⚠️ أرسل رقماً صحيحاً موجباً.")
    old = await PointsService.points_per_usd()
    await FeatureService.set_option(session, "points_currency", "points_per_usd", int(text))
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.PRICE_CHANGE,
        entity_type="points_rate",
        entity_name="سعر صرف النقاط",
        old_value=str(old),
        new_value=text,
        description="تعديل سعر صرف النقاط",
        session=session,
    )
    await state.clear()
    await message.answer(f"✅ صار كل <b>{int(text)} نقطة = 1$</b>")


# ══════════════ سقف الدفع بالنقاط ══════════════


@router.callback_query(F.data == "pts_cap")
async def ask_cap(callback: CallbackQuery, state: FSMContext):
    current = await PointsService.max_payment_percent()
    await state.set_state(AdminPointsStates.waiting_cap)
    await callback.message.answer(
        f"📊 أقصى نسبة من قيمة الطلب تُدفع بالنقاط؟\n\nالحالي: <b>{current}%</b>\n"
        "أرسل رقماً من 0 إلى 100 (0 = تعطيل الدفع بالنقاط) أو «إلغاء»:"
    )
    await callback.answer()


@router.message(AdminPointsStates.waiting_cap)
async def save_cap(message: Message, state: FSMContext, session, db_user):
    text = (message.text or "").strip()
    if text in ("إلغاء", "cancel"):
        await state.clear()
        return await message.answer("تم الإلغاء.")
    if not text.isdigit() or not (0 <= int(text) <= 100):
        return await message.answer("⚠️ أرسل رقماً بين 0 و100.")
    old = await PointsService.max_payment_percent()
    await FeatureService.set_option(
        session, "points_currency", "max_points_payment_percent", int(text)
    )
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="points_cap",
        entity_name="سقف الدفع بالنقاط",
        old_value=f"{old}%",
        new_value=f"{text}%",
        description="تعديل سقف الدفع بالنقاط",
        session=session,
    )
    await state.clear()
    await message.answer(f"✅ صار أقصى دفع بالنقاط <b>{int(text)}%</b>")


# ══════════════ عمولة التحويل ══════════════


@router.callback_query(F.data == "pts_fee")
async def ask_fee(callback: CallbackQuery, state: FSMContext):
    fee = await FeatureService.config_decimal("transfer_fee", "fee_percent", 1.0)
    await state.set_state(AdminPointsStates.waiting_rate)
    await state.update_data(field="fee")
    await callback.message.answer(
        f"💸 كم نسبة العمولة على التحويل بين المستخدمين؟\n\nالحالي: <b>{fee}%</b>\n"
        "أرسل رقماً (يدعم الكسور مثل <code>1.5</code>) أو «إلغاء»:"
    )
    await callback.answer()


@router.callback_query(F.data == "pts_limits")
async def ask_limits(callback: CallbackQuery, state: FSMContext):
    low = await FeatureService.config_decimal("transfer_fee", "min_amount_usd", 1.0)
    high = await FeatureService.config_decimal("transfer_fee", "max_amount_usd", 1000.0)
    await state.set_state(AdminPointsStates.waiting_cap)
    await state.update_data(field="limits")
    await callback.message.answer(
        f"↔️ حدود التحويل\n\nالحالي: <b>{low}$ — {high}$</b>\n"
        "أرسل الحد الأدنى ثم الأقصى، مثل: <code>1 1000</code>\n(أو «إلغاء»)"
    )
    await callback.answer()


@router.callback_query(F.data == "pts_taskcap")
async def ask_taskcap(callback: CallbackQuery, state: FSMContext):
    current = await FeatureService.config_int("tasks_system", "daily_task_limit", 10)
    await state.set_state(AdminPointsStates.waiting_cap)
    await state.update_data(field="taskcap")
    await callback.message.answer(
        f"🎯 كم مهمة يُسمح للمستخدم بإتمامها يومياً عبر كل المهام؟\n\n"
        f"الحالي: <b>{current}</b>\nأرسل رقماً (0 = بلا حد) أو «إلغاء»:"
    )
    await callback.answer()


@router.message(AdminPointsStates.waiting_rate, F.text)
async def save_fee(message: Message, state: FSMContext, session, db_user):
    data = await state.get_data()
    if data.get("field") != "fee":
        return
    text = (message.text or "").strip()
    if text in ("إلغاء", "cancel"):
        await state.clear()
        return await message.answer("تم الإلغاء.")
    try:
        value = float(text)
    except ValueError:
        return await message.answer("⚠️ أرسل رقماً مثل <code>1.5</code>")
    if not (0 <= value <= 50):
        return await message.answer("⚠️ النسبة يجب أن تكون بين 0 و50.")
    old = await FeatureService.config_decimal("transfer_fee", "fee_percent", 1.0)
    await FeatureService.set_option(session, "transfer_fee", "fee_percent", value)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="transfer_fee",
        entity_name="عمولة التحويل",
        old_value=f"{old}%",
        new_value=f"{value}%",
        description="تعديل عمولة التحويل",
        session=session,
    )
    await state.clear()
    await message.answer(f"✅ صارت عمولة التحويل <b>{value}%</b>")


@router.message(AdminPointsStates.waiting_cap, F.text)
async def save_cap_or_limits(message: Message, state: FSMContext, session, db_user):
    data = await state.get_data()
    field = data.get("field")
    text = (message.text or "").strip()
    if text in ("إلغاء", "cancel"):
        await state.clear()
        return await message.answer("تم الإلغاء.")

    if field == "limits":
        parts = text.split()
        if len(parts) != 2:
            return await message.answer("⚠️ الصيغة: <code>الأدنى الأقصى</code> مثل <code>1 1000</code>")
        try:
            low, high = float(parts[0]), float(parts[1])
        except ValueError:
            return await message.answer("⚠️ أرسل أرقاماً صحيحة.")
        if low < 0 or high <= low:
            return await message.answer("⚠️ الأقصى يجب أن يكون أكبر من الأدنى.")
        await FeatureService.set_option(session, "transfer_fee", "min_amount_usd", low)
        await FeatureService.set_option(session, "transfer_fee", "max_amount_usd", high)
        await AuditService.log(
            admin_id=db_user.id,
            action=AuditAction.UPDATE,
            entity_type="transfer_limits",
            entity_name="حدود التحويل",
            new_value=f"{low}-{high}",
            description="تعديل حدود التحويل",
            session=session,
        )
        await state.clear()
        return await message.answer(f"✅ حدود التحويل: <b>{low}$ — {high}$</b>")

    if field == "taskcap":
        if not text.isdigit():
            return await message.answer("⚠️ أرسل رقماً صحيحاً.")
        await FeatureService.set_option(session, "tasks_system", "daily_task_limit", int(text))
        await AuditService.log(
            admin_id=db_user.id,
            action=AuditAction.UPDATE,
            entity_type="tasks_daily_cap",
            entity_name="حد المهام اليومي",
            new_value=text,
            description="تعديل حد المهام اليومي",
            session=session,
        )
        await state.clear()
        return await message.answer(f"✅ حد المهام اليومي: <b>{int(text)}</b>")
    return


# ══════════════ منح / سحب نقاط ══════════════


@router.callback_query(F.data == "pts_grant")
async def ask_grant(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminPointsStates.waiting_rate)
    await state.update_data(field="grant")
    await callback.message.answer(
        "🎁 <b>منح/سحب نقاط</b>\n\n"
        "أرسل: <code>آيدي_المستخدم النقاط</code>\n"
        "مثال للمنح: <code>123456 500</code>\n"
        "مثال للسحب: <code>123456 -200</code>\n\n(أو «إلغاء»)"
    )
    await callback.answer()


@router.message(AdminPointsStates.waiting_rate, F.text.regexp(r"^-?\d+\s+-?\d+$"))
async def save_grant(message: Message, state: FSMContext, session, db_user, bot):
    data = await state.get_data()
    if data.get("field") != "grant":
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        return
    try:
        telegram_id, points = int(parts[0]), int(parts[1])
    except ValueError:
        return await message.answer("⚠️ أرسل رقمين صحيحين.")

    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        await state.clear()
        return await message.answer("⚠️ لا مستخدم بهذا الآيدي.")

    if points == 0:
        await state.clear()
        return await message.answer("⚠️ النقاط لا يمكن أن تكون صفراً.")

    if points > 0:
        await LoyaltyService.award_points(
            session,
            user.id,
            points,
            event_key=f"admin_grant:{user.id}:{db_user.id}:{points}",
            event_type="admin_grant",
            description=f"منح نقاط من الأدمن {db_user.id}",
        )
    else:
        from services.points_service import PointsService as _PS

        try:
            await _PS.spend(
                session, user.id, abs(points), f"سحب نقاط بقرار الأدمن {db_user.id}"
            )
        except Exception as exc:
            await state.clear()
            return await message.answer(f"⚠️ {exc}")

    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="user_points",
        entity_id=user.id,
        entity_name=user.username or str(user.telegram_id),
        new_value=str(points),
        description=f"{'منح' if points > 0 else 'سحب'} {abs(points)} نقطة",
        session=session,
    )
    await state.clear()
    try:
        await bot.send_message(
            user.telegram_id,
            f"⭐ {'أُضيفت' if points > 0 else 'خُصمت'} <b>{abs(points)}</b> نقطة من رصيدك.",
        )
    except Exception:
        pass
    await message.answer(f"✅ {'مُنحت' if points > 0 else 'سُحبت'} {abs(points)} نقطة من @{user.username or telegram_id}")
