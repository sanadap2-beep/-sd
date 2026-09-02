"""
الإضافات الثلاث الهدية — واجهة الأدمن.

1) 🎛 مركز القيادة: شاشة واحدة لحالة البوت كله.
2) ↩️ محرك الاسترجاع: استرجاع من أي مسار بسجل موحّد وتقرير.
3) 🛡 الحارس الذاتي: حالة الوضع الآمن وتحكم يدوي به.
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from database.models import NumberOrder, UnifiedOrder
from filters.admin_filter import IsAdmin
from services.audit_service import AuditAction, AuditService
from services.bot_command_service import (
    CockpitService,
    RefundError,
    SentinelService,
    UnifiedRefundService,
)
from services.feature_service import FeatureService
from states.states import AdminRefundStates

router = Router(name="admin_bonuses")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


# ══════════════ 1) مركز القيادة ══════════════


@router.callback_query(F.data == "admin:cockpit")
async def cockpit(callback: CallbackQuery, session):
    if not await FeatureService.enabled("bot_cockpit"):
        await callback.answer("مركز القيادة موقوف.", show_alert=True)
        return
    data = await CockpitService.snapshot(session)
    sentinel = await SentinelService.status(session)

    text = CockpitService.render(data)
    if sentinel["safe_mode"]:
        text += (
            "\n\n🛡 <b>الوضع الآمن مفعّل</b>\n"
            f"   السبب: {sentinel['reason'] or '—'}\n"
            f"   معطّل: {len(sentinel['currently_disabled'])} ميزة خطرة"
        )

    rows = [
        [
            InlineKeyboardButton(text="🔄 تحديث", callback_data="admin:cockpit"),
            InlineKeyboardButton(text="🛡 الحارس", callback_data="bonus:sentinel"),
        ],
        [
            InlineKeyboardButton(text="↩️ الاسترجاع", callback_data="bonus:refund"),
            InlineKeyboardButton(text="🧩 الإضافات", callback_data="admin:features"),
        ],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")],
    ]
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


# ══════════════ 2) محرك الاسترجاع ══════════════


@router.callback_query(F.data == "bonus:refund")
async def refund_home(callback: CallbackQuery, session):
    if not await FeatureService.enabled("unified_refund"):
        await callback.answer("محرك الاسترجاع موقوف.", show_alert=True)
        return
    report = await UnifiedRefundService.report(session, 30)
    if report["by_reason"]:
        lines = [
            f"• {row['reason']}: {row['count']} عملية — {row['amount_usd']}$"
            for row in report["by_reason"]
        ]
        breakdown = "\n".join(lines)
    else:
        breakdown = "لا استرجاعات في آخر 30 يوماً."

    rows = [
        [InlineKeyboardButton(text="↩️ استرجاع طلب رقم", callback_data="bonus:refnum")],
        [InlineKeyboardButton(text="↩️ استرجاع طلب رشق/ألعاب", callback_data="bonus:refuni")],
        [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:cockpit")],
    ]
    await callback.message.edit_text(
        "↩️ <b>محرك الاسترجاع الموحّد</b>\n\n"
        f"آخر 30 يوماً: <b>{report['count']}</b> عملية · <b>{report['total_usd']}$</b>\n\n"
        f"{breakdown}\n\n"
        "كل استرجاع يمر من سلطة واحدة بمفتاح idempotent،\n"
        "فلا يُدفع مرتين ويُسجَّل سببه.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "bonus:refnum")
async def refund_number_ask(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminRefundStates.waiting_number_refund)
    await callback.message.answer(
        "↩️ <b>استرجاع طلب رقم</b>\n\n"
        "أرسل: <code>رقم_الطلب السبب</code>\n"
        "الأسباب: no_code · provider_failure · wrong_service · "
        "duplicate · admin_goodwill\n\n"
        "مثال: <code>1234 no_code</code>"
    )
    await callback.answer()


@router.message(AdminRefundStates.waiting_number_refund)
async def refund_number_submit(message: Message, state: FSMContext, session, db_user):
    parts = (message.text or "").split(maxsplit=1)
    await state.clear()
    if not parts:
        return await message.answer("⚠️ الصيغة: <code>رقم_الطلب السبب</code>")
    try:
        order_id = int(parts[0])
    except ValueError:
        return await message.answer("⚠️ رقم الطلب يجب أن يكون رقماً.")
    reason = parts[1].strip() if len(parts) > 1 else "admin_goodwill"

    try:
        result = await UnifiedRefundService.refund_number_order(
            session, order_id, user_id=0, reason=reason, admin_id=db_user.id
        )
    except RefundError as exc:
        return await message.answer(f"⚠️ {exc}")

    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="refund",
        entity_id=result["refund_id"],
        entity_name=f"طلب رقم #{order_id}",
        new_value=str(result["amount_usd"]),
        description=f"استرجاع موحّد: {reason}",
        session=session,
    )
    await message.answer(
        f"✅ <b>تم الاسترجاع #{result['refund_id']}</b>\n\n"
        f"المبلغ: <b>{result['amount_usd']}$</b>\nالسبب: {reason}"
    )


@router.callback_query(F.data == "bonus:refuni")
async def refund_unified_ask(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminRefundStates.waiting_unified_refund)
    await callback.message.answer(
        "↩️ <b>استرجاع طلب رشق/ألعاب</b>\n\n"
        "أرسل: <code>رقم_الطلب السبب</code>\n"
        "مثال: <code>5678 provider_failure</code>"
    )
    await callback.answer()


@router.message(AdminRefundStates.waiting_unified_refund)
async def refund_unified_submit(message: Message, state: FSMContext, session, db_user):
    parts = (message.text or "").split(maxsplit=1)
    await state.clear()
    if not parts:
        return await message.answer("⚠️ الصيغة: <code>رقم_الطلب السبب</code>")
    try:
        order_id = int(parts[0])
    except ValueError:
        return await message.answer("⚠️ رقم الطلب يجب أن يكون رقماً.")
    reason = parts[1].strip() if len(parts) > 1 else "admin_goodwill"

    try:
        result = await UnifiedRefundService.refund_unified_order(
            session, order_id, user_id=0, reason=reason, admin_id=db_user.id
        )
    except RefundError as exc:
        return await message.answer(f"⚠️ {exc}")

    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="refund",
        entity_id=result["refund_id"],
        entity_name=f"طلب #{order_id}",
        new_value=str(result["amount_usd"]),
        description=f"استرجاع موحّد: {reason}",
        session=session,
    )
    await message.answer(
        f"✅ <b>تم الاسترجاع #{result['refund_id']}</b>\n\n"
        f"المبلغ: <b>{result['amount_usd']}$</b>\nالسبب: {reason}"
    )


# ══════════════ 3) الحارس الذاتي ══════════════


@router.callback_query(F.data == "bonus:sentinel")
async def sentinel_home(callback: CallbackQuery, session):
    if not await FeatureService.enabled("self_heal_sentinel"):
        await callback.answer("الحارس الذاتي موقوف.", show_alert=True)
        return
    status = await SentinelService.status(session)
    if status["currently_disabled"]:
        disabled = "\n".join(f"   ⚪ {key}" for key in status["currently_disabled"])
    else:
        disabled = "   لا شيء معطّل"

    rows = []
    if status["safe_mode"]:
        rows.append(
            [InlineKeyboardButton(text="✅ إخراج من الوضع الآمن", callback_data="bonus:sentoff")]
        )
    else:
        rows.append(
            [InlineKeyboardButton(text="🛡 إدخال الوضع الآمن يدوياً", callback_data="bonus:senton")]
        )
    rows.append([InlineKeyboardButton(text="⚙️ ضبط العتبة", callback_data="bonus:sentthresh")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:cockpit")])

    await callback.message.edit_text(
        "🛡 <b>الحارس الذاتي</b>\n\n"
        f"الوضع الآمن: {'🔴 مفعّل' if status['safe_mode'] else '🟢 غير مفعّل'}\n"
        + (f"السبب: {status['reason']}\n" if status["reason"] else "")
        + f"معدل الأخطاء: <b>{status['error_rate']}</b>\n"
        f"العتبة: {status['threshold']} خطأ في {status['window_minutes']} دقيقة\n\n"
        "<b>الميزات الخطرة (تُعطَّل تلقائياً):</b>\n"
        f"{disabled}\n\n"
        "الفكرة: ارتفاع الأخطاء يُلاحظ قبل أن يشتكي العملاء،\n"
        "فتُعطَّل الميزات التي تحرّك أموالاً وتبقى الأساسية.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data == "bonus:senton")
async def sentinel_engage(callback: CallbackQuery, session, db_user):
    disabled = await SentinelService.engage_safe_mode(session, "تفعيل يدوي من الأدمن")
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.DEACTIVATE,
        entity_type="safe_mode",
        entity_name="الوضع الآمن",
        new_value=f"{len(disabled)} ميزة",
        description="إدخال البوت الوضع الآمن يدوياً",
        session=session,
    )
    await callback.answer(f"🛡 عُطّلت {len(disabled)} ميزة خطرة.")
    await sentinel_home(callback)


@router.callback_query(F.data == "bonus:sentoff")
async def sentinel_disengage(callback: CallbackQuery, session, db_user):
    restored = await SentinelService.disengage_safe_mode(session)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.ACTIVATE,
        entity_type="safe_mode",
        entity_name="الوضع الآمن",
        new_value=f"{len(restored)} ميزة",
        description="إخراج البوت من الوضع الآمن يدوياً",
        session=session,
    )
    await callback.answer(f"✅ أُعيدت {len(restored)} ميزة.")
    await sentinel_home(callback)


@router.callback_query(F.data == "bonus:sentthresh")
async def sentinel_threshold_ask(callback: CallbackQuery, state: FSMContext):
    current = await SentinelService.error_threshold()
    window = await SentinelService.window_minutes()
    await state.set_state(AdminRefundStates.waiting_sentinel_config)
    await callback.message.answer(
        "⚙️ <b>ضبط الحارس</b>\n\n"
        f"الحالي: {current} خطأ في {window} دقيقة\n\n"
        "أرسل: <code>العتدة النافذة_بالدقائق</code>\n"
        "مثال: <code>20 10</code>"
    )
    await callback.answer()


@router.message(AdminRefundStates.waiting_sentinel_config)
async def sentinel_threshold_submit(message: Message, state: FSMContext, session, db_user):
    parts = (message.text or "").split()
    await state.clear()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return await message.answer("⚠️ الصيغة: <code>العتبة النافذة</code> مثل <code>20 10</code>")
    threshold, window = int(parts[0]), int(parts[1])
    if threshold < 1 or window < 1:
        return await message.answer("⚠️ القيمتان يجب أن تكونا موجبتين.")

    await FeatureService.set_option(session, "self_heal_sentinel", "error_threshold", threshold)
    await FeatureService.set_option(session, "self_heal_sentinel", "window_minutes", window)
    await AuditService.log(
        admin_id=db_user.id,
        action=AuditAction.UPDATE,
        entity_type="sentinel",
        entity_name="عتبة الحارس",
        new_value=f"{threshold}/{window}",
        description="ضبط عتبة الحارس الذاتي",
        session=session,
    )
    await message.answer(f"✅ العتبة: {threshold} خطأ في {window} دقيقة")
