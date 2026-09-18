"""Admin review for user withdrawal requests."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from database.models import User, WithdrawalRequest
from filters.admin_filter import IsAdmin
from services.notification_service import NotificationService
from services.withdrawal_service import WithdrawalService

router = Router(name="admin_withdrawals")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:withdrawals")
async def withdrawals_home(callback: CallbackQuery, session):
    requests = await WithdrawalService.pending(session, 20)
    if not requests:
        await callback.message.edit_text(
            "💸 لا توجد طلبات سحب معلقة.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")]]
            ),
        )
        await callback.answer()
        return
    rows = [
        [
            InlineKeyboardButton(
                text=f"#{req.id} {req.payout_amount} {req.currency} · {req.method}",
                callback_data=f"admin:withdraw_view:{req.id}",
            )
        ]
        for req in requests
    ]
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:main")])
    await callback.message.edit_text(
        "💸 <b>طلبات السحب المعلقة</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:withdraw_view:"))
async def withdrawal_view(callback: CallbackQuery, session):
    req = await session.get(WithdrawalRequest, int(callback.data.rsplit(":", 1)[1]))
    if req is None:
        await callback.answer("الطلب غير موجود.", show_alert=True)
        return
    user = await session.get(User, req.user_id)
    await callback.message.edit_text(
        f"💸 <b>طلب سحب #{req.id}</b>\n\n"
        f"👤 المستخدم: <code>{user.telegram_id if user else req.user_id}</code>\n"
        f"الطريقة: {req.method} {req.network or ''}\n"
        f"المبلغ المحجوز: {req.amount_usd}$\n"
        f"المطلوب دفعه: <b>{req.payout_amount} {req.currency}</b>\n"
        f"الحالة: {req.status}\n\n"
        f"العنوان:\n<code>{req.payout_address}</code>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ تم الدفع", callback_data=f"admin:withdraw_paid:{req.id}")],
                [InlineKeyboardButton(text="❌ رفض وإرجاع الرصيد", callback_data=f"admin:withdraw_reject:{req.id}", style="danger")],
                [InlineKeyboardButton(text="⬅️ رجوع", callback_data="admin:withdrawals")],
            ]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:withdraw_paid:"))
async def withdrawal_paid(callback: CallbackQuery, session, db_user, bot):
    req_id = int(callback.data.rsplit(":", 1)[1])
    req = await WithdrawalService.complete(session, req_id, db_user.id)
    if req is None:
        await callback.answer("لا يمكن معالجة هذا الطلب.", show_alert=True)
        return
    user = await session.get(User, req.user_id)
    if user:
        await NotificationService(bot).notify_user(
            user.telegram_id,
            f"✅ <b>تم دفع طلب السحب #{req.id}</b>\n\n"
            f"المبلغ: <b>{req.payout_amount} {req.currency}</b>\n"
            "شكراً لاستخدامك البوت.",
        )
    await callback.answer("✅ تم تعليم السحب كمدفوع.")
    await withdrawals_home(callback, session)


@router.callback_query(F.data.startswith("admin:withdraw_reject:"))
async def withdrawal_reject(callback: CallbackQuery, session, db_user, bot):
    req_id = int(callback.data.rsplit(":", 1)[1])
    req = await WithdrawalService.reject(session, req_id, db_user.id)
    if req is None:
        await callback.answer("لا يمكن معالجة هذا الطلب.", show_alert=True)
        return
    user = await session.get(User, req.user_id)
    if user:
        await NotificationService(bot).notify_user(
            user.telegram_id,
            f"↩️ <b>تم رفض طلب السحب #{req.id}</b>\n\n"
            f"أُعيد إلى رصيدك: <b>{req.amount_usd}$</b>",
        )
    await callback.answer("↩️ تم الرفض وإرجاع الرصيد.")
    await withdrawals_home(callback, session)
