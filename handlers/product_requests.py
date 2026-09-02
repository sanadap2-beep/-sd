"""سوق الطلبات: المستخدمون يطلبون خدمات ويصوتون عليها."""

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.models import ProductRequestStatus, User
from keyboards.main_menu import back_to_main_kb
from keyboards.product_requests import (
    admin_product_request_detail_kb,
    product_request_detail_kb,
    product_requests_kb,
)
from services.notification_service import NotificationService
from services.product_request_service import (
    ProductRequestError,
    ProductRequestService,
)
from states.states import ProductRequestStates

router = Router(name="product_requests")

_STATUS_LABELS = {
    ProductRequestStatus.OPEN: "🟢 مفتوح",
    ProductRequestStatus.IN_REVIEW: "🔎 قيد الدراسة",
    ProductRequestStatus.FULFILLED: "✅ تم توفيره",
    ProductRequestStatus.REJECTED: "❌ مرفوض",
}


def _status_label(status) -> str:
    return _STATUS_LABELS.get(status, getattr(status, "value", str(status)))


@router.callback_query(F.data.in_({"menu:product_request", "market:requests"}))
async def product_requests_page(callback: CallbackQuery, session):
    requests = await ProductRequestService.get_open(session, limit=20)
    await callback.answer()
    if not requests:
        await callback.message.edit_text(
            "📣 <b>سوق الطلبات</b>\n\nلا توجد طلبات مفتوحة بعد. كن أول من يطلب خدمة جديدة!",
            reply_markup=product_requests_kb([]),
        )
        return
    lines = [
        "📣 <b>سوق الطلبات</b>\n",
        "صوّت على الخدمات التي تريدها؛ الأكثر طلباً تصل أولاً إلى الإدارة.",
        "",
    ]
    for request in requests:
        lines.append(
            f"👍 <b>{request.votes_count}</b> · "
            f"{escape(request.title)} · {_status_label(request.status)}"
        )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=product_requests_kb(requests),
    )


@router.callback_query(F.data == "market:request:new")
async def product_request_new(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(ProductRequestStates.waiting_request)
    await callback.answer()
    await callback.message.edit_text(
        "📣 <b>اطلب خدمة جديدة</b>\n\n"
        "أرسل اسم الخدمة في السطر الأول، ثم أي تفاصيل أو مواصفات في "
        "الأسطر التالية (حتى 1500 حرف):",
        reply_markup=back_to_main_kb(),
    )


@router.message(ProductRequestStates.waiting_request)
async def product_request_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
    bot,
):
    raw = (message.text or "").strip()
    if len(raw) < 2:
        await message.answer("⚠️ اكتب اسم الخدمة المطلوبة.")
        return
    lines = raw.splitlines()
    title = lines[0][:128]
    details = "\n".join(lines[1:]).strip() or None
    try:
        request, created = await ProductRequestService.create(
            session,
            db_user.id,
            title,
            details,
        )
    except ProductRequestError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    admin_text = (
        "📣 <b>طلب خدمة من السوق</b>\n\n"
        f"🆔 الطلب: <b>#{request.id}</b>\n"
        f"👍 الأصوات: <b>{request.votes_count}</b>\n"
        f"👤 المستخدم: <code>{db_user.telegram_id}</code>\n"
        f"📝 الخدمة: <b>{escape(request.title)}</b>\n"
        f"📄 التفاصيل: {escape(request.details or '—')}"
    )
    await NotificationService(bot).notify_admin(
        admin_text,
        reply_markup=admin_product_request_detail_kb(
            request.id,
            request.status.value,
        ),
    )
    await state.clear()
    if created:
        message_text = (
            "✅ تم تسجيل طلبك في سوق الخدمات.\n"
            f"رقم الطلب: <code>#{request.id}</code>\n"
            "يمكن للمستخدمين الآخرين التصويت عليه ليرتفع إلى الإدارة."
        )
    else:
        message_text = (
            "👍 هذا الطلب موجود مسبقاً، وتم احتساب تصويتك عليه.\n"
            f"إجمالي الأصوات: <b>{request.votes_count}</b>"
        )
    await message.answer(message_text, reply_markup=back_to_main_kb())


@router.callback_query(F.data == "market:request:mine")
async def my_product_requests(
    callback: CallbackQuery,
    session,
    db_user: User,
):
    requests = await ProductRequestService.get_for_user(session, db_user.id, limit=20)
    await callback.answer()
    if not requests:
        await callback.message.edit_text(
            "📋 لا توجد لديك طلبات خدمات بعد.",
            reply_markup=product_requests_kb([]),
        )
        return
    lines = ["📋 <b>طلباتي في السوق</b>\n"]
    for request in requests:
        lines.append(
            f"#{request.id} · {_status_label(request.status)} · "
            f"{escape(request.title)} · 👍 {request.votes_count}"
        )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=product_requests_kb(requests),
    )


@router.callback_query(F.data.startswith("market:request:view:"))
async def product_request_view(
    callback: CallbackQuery,
    session,
    db_user: User,
    answer_callback: bool = True,
):
    request_id = int(callback.data.split(":")[3])
    request = await ProductRequestService.get_one(session, request_id)
    if request is None:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    if answer_callback:
        await callback.answer()
    text = (
        f"📣 <b>طلب الخدمة #{request.id}</b>\n\n"
        f"📝 <b>{escape(request.title)}</b>\n"
        f"📊 الحالة: {_status_label(request.status)}\n"
        f"👍 الأصوات: <b>{request.votes_count}</b>\n\n"
        f"📄 التفاصيل:\n{escape(request.details or 'لا توجد تفاصيل')}"
    )
    can_vote = request.status in (
        ProductRequestStatus.OPEN,
        ProductRequestStatus.IN_REVIEW,
    )
    await callback.message.edit_text(
        text,
        reply_markup=product_request_detail_kb(request.id, can_vote),
    )


@router.callback_query(F.data.startswith("market:request:vote:"))
async def product_request_vote(
    callback: CallbackQuery,
    session,
    db_user: User,
):
    request_id = int(callback.data.split(":")[3])
    try:
        voted = await ProductRequestService.vote(session, request_id, db_user.id)
    except ProductRequestError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(
        "👍 تم احتساب صوتك." if voted else "ℹ️ صوتت على هذا الطلب مسبقاً.",
        show_alert=True,
    )
    await product_request_view(callback, session, db_user, answer_callback=False)
