"""
مراجعة طلبات «اشحن لأهلك» من لوحة الأدمن (ميزة mobile_topup_gift).

الأدمن يشاهد الطلب ورقم المستلم والمبلغ، ثم:
- قبول = إشعار المستخدم بأن الشحن تم/سيُشحن.
- رفض = يُطلب سبب ويرسل إشعاراً بالمستخدم.
"""

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from database.models import TopupGiftRequest, User
from filters.admin_filter import IsAdmin
from services.notification_service import NotificationService
from states.states import AdminTopupGiftStates
from keyboards.admin import admin_topup_gifts_kb, admin_topup_gift_decision_kb

router = Router(name="admin_topup_gifts")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

STATUS_LABELS = {"pending": "⏳ بانتظار المراجعة", "approved": "✅ مقبول", "rejected": "❌ مرفوض"}
OPERATOR_LABELS = {"mtn": "MTN", "syriatel": "سيريتل"}


def _operator_name(op: str) -> str:
    return OPERATOR_LABELS.get(op, op)


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")]]
    )


async def _list_requests(session) -> list[TopupGiftRequest]:
    result = await session.execute(
        select(TopupGiftRequest).order_by(TopupGiftRequest.created_at.desc()).limit(20)
    )
    return list(result.scalars().all())


# ══════════════ القائمة ══════════════


async def _render_list(target, session):
    requests = await _list_requests(session)
    if not requests:
        text = (
            "🎁 لا توجد طلبات «اشحن لأهلك» بعد.\n\n"
            "المستخدم يختار المشغّل والمبلغ ورقم المستلم، ويصل الطلب هنا "
            "لتنفيذه يدوياً عبر خدمات المشغّل."
        )
        kb = _back_kb()
    else:
        text = (
            "🎁 <b>طلبات «اشحن لأهلك»</b>\n\n"
            "⏳ = بانتظار المراجعة | ✅ = مقبول | ❌ = مرفوض\n"
            "اضغط على أي طلب لتفاصيله."
        )
        kb = admin_topup_gifts_kb(requests)

    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=kb)
        except Exception:
            await target.message.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data == "admin:topup_gifts")
async def topup_gifts_list(callback: CallbackQuery, session):
    await _render_list(callback, session)
    await callback.answer()


# ══════════════ تفاصيل الطلب ══════════════


@router.callback_query(F.data.startswith("admin:topup_view:"))
async def topup_view(callback: CallbackQuery, session):
    request_id = int(callback.data.split(":")[2])
    request = await session.get(TopupGiftRequest, request_id)
    if request is None:
        await callback.answer("الطلب غير موجود.", show_alert=True)
        return

    user = await session.get(User, request.user_id)
    note = request.note or "—"
    created = request.created_at.strftime("%Y-%m-%d %H:%M")

    text = (
        f"🎁 <b>طلب «اشحن لأهلك» #{request.id}</b>\n\n"
        f"👤 المستخدم: <code>{user.telegram_id if user else request.user_id}</code> "
        f"(@{user.username if user else '-'})\n"
        f"📡 المشغّل: <b>{_operator_name(request.operator)}</b>\n"
        f"💵 المبلغ: <b>{request.amount_usd}$</b>\n"
        f"📱 رقم المستلم: <code>{request.recipient_number}</code>\n"
        f"💬 ملاحظة: {note}\n"
        f"الحالة: {STATUS_LABELS.get(request.status, request.status)}\n"
        f"🕐 أُنشئ: {created}"
    )
    await callback.message.edit_text(text, reply_markup=admin_topup_gift_decision_kb(request.id))
    await callback.answer()


# ══════════════ قبول ══════════════


@router.callback_query(F.data.startswith("admin:topup_approve:"))
async def topup_approve(callback: CallbackQuery, session, db_user, bot):
    request_id = int(callback.data.split(":")[2])
    request = await session.get(TopupGiftRequest, request_id)
    if request is None or request.status != "pending":
        await callback.answer("لا يمكن معالجة هذا الطلب.", show_alert=True)
        return

    request.status = "approved"
    request.admin_id = db_user.id
    request.processed_at = datetime.now(timezone.utc)
    await session.commit()

    user = await session.get(User, request.user_id)
    if user:
        await NotificationService(bot).notify_user(
            user.telegram_id,
            f"✅ <b>تم تنفيذ طلب الشحن!</b>\n\n"
            f"📡 المشغّل: <b>{_operator_name(request.operator)}</b>\n"
            f"💵 المبلغ: <b>{request.amount_usd}$</b>\n"
            f"📱 رقم المستلم: <code>{request.recipient_number}</code>\n\n"
            "شكراً لاستخدامك البوت.",
        )

    await _render_list(callback, session)
    await callback.answer("✅ تم اعتماد الطلب وإشعار المستخدم.")


# ══════════════ رفض (يتطلب سبباً) ══════════════


@router.callback_query(F.data.startswith("admin:topup_reject:"))
async def topup_reject_start(callback: CallbackQuery, state: FSMContext):
    request_id = int(callback.data.split(":")[2])
    await state.update_data(topup_reject_id=request_id)
    await callback.message.edit_text(
        "❌ <b>رفض طلب «اشحن لأهلك»</b>\n\n"
        "أرسل سبب الرفض ليصل للمستخدم:\n"
        "(مثال: رقم المستلم غير صحيح، أعد المحاولة)",
        reply_markup=_back_kb(),
    )
    await state.set_state(AdminTopupGiftStates.waiting_reject_reason)
    await callback.answer()


@router.message(AdminTopupGiftStates.waiting_reject_reason)
async def topup_reject_reason_received(
    message: Message,
    state: FSMContext,
    session,
    db_user,
    bot,
):
    reason = (message.text or "").strip()[:255]
    if not reason:
        await message.answer("⚠️ أرسل سبب الرفض نصاً.")
        return

    data = await state.get_data()
    request_id = int(data.get("topup_reject_id", 0))
    request = await session.get(TopupGiftRequest, request_id)
    if request is None or request.status != "pending":
        await message.answer("❌ الطلب غير موجود أو سبقت معالجته.")
        await state.clear()
        return

    request.status = "rejected"
    request.admin_id = db_user.id
    request.reject_reason = reason
    request.processed_at = datetime.now(timezone.utc)
    await session.commit()

    user = await session.get(User, request.user_id)
    if user:
        await NotificationService(bot).notify_user(
            user.telegram_id,
            f"❌ <b>تم رفض طلب الشحن</b>\n\n"
            f"📱 رقم المستلم: <code>{request.recipient_number}</code>\n"
            f"📝 السبب: {reason}\n\n"
            "يمكنك المحاولة مجدداً من القائمة.",
        )

    await message.answer("❌ تم رفض الطلب وإشعار المستخدم بالسبب.")
    await state.clear()

    await _render_list(message, session)