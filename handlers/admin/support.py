"""إدارة تذاكر الدعم من لوحة الأدمن."""

from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload

from database.models import SupportTicket, SupportTicketStatus, User
from filters.admin_filter import IsAdmin
from keyboards.admin import admin_back_kb
from keyboards.support import (
    admin_ticket_kb,
    admin_tickets_kb,
)
from services.notification_service import NotificationService
from states.states import AdminTicketStates

router = Router(name="admin_support")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_PAGE_SIZE = 8
_STATUS_LABELS = {
    SupportTicketStatus.OPEN: "🟢 مفتوحة",
    SupportTicketStatus.IN_PROGRESS: "🔄 قيد المتابعة",
    SupportTicketStatus.RESOLVED: "✅ محلولة",
    SupportTicketStatus.CLOSED: "⚪ مغلقة",
}


def _status_label(status) -> str:
    return _STATUS_LABELS.get(status, getattr(status, "value", str(status)))


async def _get_ticket(session, ticket_id: int):
    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.user))
        .where(SupportTicket.id == ticket_id)
    )
    return result.scalar_one_or_none()


async def _render_tickets(callback: CallbackQuery, session, page: int = 0):
    total = (await session.execute(select(func.count(SupportTicket.id)))).scalar_one()
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    result = await session.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.user))
        .order_by(desc(SupportTicket.created_at))
        .limit(_PAGE_SIZE)
        .offset(page * _PAGE_SIZE)
    )
    tickets = list(result.scalars().all())
    open_count = (
        await session.execute(
            select(func.count(SupportTicket.id)).where(
                SupportTicket.status.in_(
                    [
                        SupportTicketStatus.OPEN,
                        SupportTicketStatus.IN_PROGRESS,
                    ]
                )
            )
        )
    ).scalar_one()

    text = (
        "🎫 <b>تذاكر الدعم</b>\n\n"
        f"⏳ المفتوحة: <b>{open_count}</b>\n"
        f"📋 الإجمالي: {total}\n"
        f"📄 الصفحة: {page + 1}/{total_pages}\n\n"
    )
    text += "اضغط على تذكرة لعرضها والرد عليها." if tickets else "لا توجد تذاكر."
    await callback.message.edit_text(
        text,
        reply_markup=admin_tickets_kb(tickets, page, total_pages),
    )


@router.callback_query(F.data == "admin:tickets")
async def tickets_list(callback: CallbackQuery, session):
    await callback.answer()
    await _render_tickets(callback, session)


@router.callback_query(F.data.startswith("admin:tickets:"))
async def tickets_page(callback: CallbackQuery, session):
    try:
        page = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        page = 0
    await callback.answer()
    await _render_tickets(callback, session, page)


async def _render_ticket_detail(callback: CallbackQuery, ticket: SupportTicket):
    user = ticket.user
    text = (
        f"🎫 <b>تذكرة الدعم #{ticket.id}</b>\n\n"
        f"📊 الحالة: <b>{_status_label(ticket.status)}</b>\n"
        f"👤 المستخدم: {escape(user.full_name if user else '—')} "
        f"(<code>{user.telegram_id if user else '—'}</code>)\n"
        f"📝 العنوان: <b>{escape(ticket.subject)}</b>\n"
        f"📅 التاريخ: {ticket.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"<b>رسالة المستخدم:</b>\n{escape(ticket.message)}"
    )
    if ticket.admin_reply:
        text += f"\n\n<b>آخر رد:</b>\n{escape(ticket.admin_reply)}"
    if ticket.admin_id:
        text += f"\n👨‍💼 الأدمن المعالج: <code>{ticket.admin_id}</code>"

    await callback.message.edit_text(
        text,
        reply_markup=admin_ticket_kb(
            ticket.id,
            getattr(ticket.status, "value", ticket.status),
        ),
    )


@router.callback_query(F.data.startswith("admin:ticket_view:"))
async def ticket_view(callback: CallbackQuery, session):
    ticket_id = int(callback.data.split(":")[2])
    ticket = await _get_ticket(session, ticket_id)
    if ticket is None:
        await callback.answer("⚠️ التذكرة غير موجودة.", show_alert=True)
        return
    await callback.answer()
    await _render_ticket_detail(callback, ticket)


@router.callback_query(F.data.startswith("admin:ticket_reply:"))
async def ticket_reply_start(
    callback: CallbackQuery,
    state: FSMContext,
    session,
):
    ticket_id = int(callback.data.split(":")[2])
    ticket = await _get_ticket(session, ticket_id)
    if ticket is None or ticket.status in (
        SupportTicketStatus.RESOLVED,
        SupportTicketStatus.CLOSED,
    ):
        await callback.answer("⚠️ هذه التذكرة مغلقة.", show_alert=True)
        return
    await state.update_data(ticket_id=ticket_id)
    await state.set_state(AdminTicketStates.waiting_reply)
    await callback.answer()
    await callback.message.answer(
        f"✉️ أرسل ردك على التذكرة <b>#{ticket_id}</b>:",
        reply_markup=admin_back_kb(),
    )


@router.message(AdminTicketStates.waiting_reply)
async def ticket_reply_received(
    message: Message,
    state: FSMContext,
    session,
    db_user: User,
    bot,
):
    reply = (message.text or "").strip()
    if len(reply) < 2:
        await message.answer("⚠️ اكتب رداً واضحاً.")
        return
    if len(reply) > 2000:
        await message.answer("⚠️ الحد الأقصى للرد 2000 حرف.")
        return

    data = await state.get_data()
    ticket = await _get_ticket(session, int(data["ticket_id"]))
    if ticket is None:
        await message.answer("⚠️ التذكرة غير موجودة.")
        await state.clear()
        return

    ticket.admin_reply = reply
    ticket.admin_id = db_user.id
    ticket.status = SupportTicketStatus.IN_PROGRESS
    await session.commit()

    if ticket.user:
        await NotificationService(bot).notify_user(
            ticket.user.telegram_id,
            f"✉️ <b>رد جديد على تذكرتك #{ticket.id}</b>\n\n{escape(reply)}",
        )
    await message.answer(
        f"✅ تم إرسال الرد على التذكرة #{ticket.id}.",
        reply_markup=admin_back_kb(),
    )
    await state.clear()


@router.callback_query(F.data.startswith("admin:ticket_resolve:"))
async def ticket_resolve(
    callback: CallbackQuery,
    session,
    bot,
    db_user: User,
):
    ticket_id = int(callback.data.split(":")[2])
    ticket = await _get_ticket(session, ticket_id)
    if ticket is None:
        await callback.answer("⚠️ التذكرة غير موجودة.", show_alert=True)
        return

    if ticket.status in (
        SupportTicketStatus.RESOLVED,
        SupportTicketStatus.CLOSED,
    ):
        await callback.answer("ℹ️ التذكرة محلولة مسبقاً.", show_alert=True)
        return

    ticket.status = SupportTicketStatus.RESOLVED
    ticket.admin_id = db_user.id
    ticket.resolved_at = datetime.utcnow()
    await session.commit()

    if ticket.user:
        await NotificationService(bot).notify_user(
            ticket.user.telegram_id,
            f"✅ تم حل تذكرة الدعم #{ticket.id}.\n\nإذا بقيت المشكلة يمكنك فتح تذكرة جديدة.",
        )
    await callback.answer("✅ تم حل التذكرة.")
    await _render_ticket_detail(callback, ticket)
