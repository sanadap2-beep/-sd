"""إدارة طلبات السوق التي يقترحها المستخدمون."""

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery

from database.models import ProductRequestStatus
from filters.admin_filter import IsAdmin
from keyboards.product_requests import (
    admin_product_request_detail_kb,
    admin_product_requests_kb,
)
from services.notification_service import NotificationService
from services.product_request_service import ProductRequestService

router = Router(name="admin_product_requests")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


async def _render_market_requests(callback: CallbackQuery, session):
    requests = await ProductRequestService.get_open(session, limit=50)
    await callback.message.edit_text(
        "📈 <b>طلبات السوق</b>\n\n"
        "هذه القائمة مرتبة حسب عدد الأصوات؛ استخدمها لاكتشاف الخدمات "
        "التي يريدها العملاء فعلياً.",
        reply_markup=admin_product_requests_kb(requests),
    )


@router.callback_query(F.data == "admin:market_requests")
async def market_requests(callback: CallbackQuery, session):
    await callback.answer()
    await _render_market_requests(callback, session)


@router.callback_query(F.data.startswith("admin:market_request:view:"))
async def market_request_view(
    callback: CallbackQuery,
    session,
    answer_callback: bool = True,
):
    request_id = int(callback.data.split(":")[3])
    request = await ProductRequestService.get_one(session, request_id)
    if request is None:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    user = request.user
    await callback.message.edit_text(
        f"📈 <b>طلب سوق #{request.id}</b>\n\n"
        f"📝 الخدمة: <b>{escape(request.title)}</b>\n"
        f"📊 الحالة: {request.status.value}\n"
        f"👍 الأصوات: <b>{request.votes_count}</b>\n"
        f"👤 صاحب الطلب: {user.telegram_id if user else '—'}\n\n"
        f"📄 التفاصيل:\n{escape(request.details or '—')}\n\n"
        f"📝 ملاحظة الإدارة: {escape(request.admin_note or '—')}",
        reply_markup=admin_product_request_detail_kb(request.id, request.status.value),
    )
    if answer_callback:
        await callback.answer()


async def _set_request_status(
    callback: CallbackQuery,
    session,
    bot,
    admin_id: int,
    status: ProductRequestStatus,
):
    request_id = int(callback.data.split(":")[3])
    request = await ProductRequestService.set_status(session, request_id, status, admin_id)
    if request is None:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    if status == ProductRequestStatus.FULFILLED and request.user:
        await NotificationService(bot).notify_user(
            request.user.telegram_id,
            "🎉 <b>تم توفير الخدمة التي طلبتها!</b>\n\n"
            f"📦 الخدمة: {escape(request.title)}\n"
            "افتح الكتالوج الآن لتجدها ضمن المنتجات المتاحة.",
        )
    await callback.answer("✅ تم تحديث حالة الطلب.")
    await market_request_view(callback, session, answer_callback=False)


@router.callback_query(F.data.startswith("admin:market_request:review:"))
async def market_request_review(callback: CallbackQuery, session, db_user):
    await _set_request_status(
        callback,
        session,
        callback.bot,
        db_user.id,
        ProductRequestStatus.IN_REVIEW,
    )


@router.callback_query(F.data.startswith("admin:market_request:fulfill:"))
async def market_request_fulfill(callback: CallbackQuery, session, db_user, bot):
    await _set_request_status(
        callback,
        session,
        bot,
        db_user.id,
        ProductRequestStatus.FULFILLED,
    )


@router.callback_query(F.data.startswith("admin:market_request:reject:"))
async def market_request_reject(callback: CallbackQuery, session, db_user):
    await _set_request_status(
        callback,
        session,
        callback.bot,
        db_user.id,
        ProductRequestStatus.REJECTED,
    )
