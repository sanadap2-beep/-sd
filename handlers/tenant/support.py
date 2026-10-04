"""Tenant support: tickets go to the merchant (never to the platform admin)."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import desc, select

from database.models import SupportTicket, SupportTicketStatus, Tenant, User
from services.html_guard import esc
from states.states import TenantSupportStates

router = Router(name="tenant_support")

_STATUS = {
    SupportTicketStatus.OPEN: "🟢 مفتوحة",
    SupportTicketStatus.IN_PROGRESS: "🔄 قيد المتابعة",
    SupportTicketStatus.RESOLVED: "✅ محلولة",
    SupportTicketStatus.CLOSED: "⚪ مغلقة",
}


@router.callback_query(F.data == "t:support")
async def tenant_support_menu(callback: CallbackQuery, session, db_user: User, tenant: Tenant):
    result = await session.execute(
        select(SupportTicket)
        .where(
            SupportTicket.user_id == db_user.id,
            SupportTicket.tenant_id == tenant.id,
        )
        .order_by(desc(SupportTicket.id))
        .limit(5)
    )
    tickets = list(result.scalars().all())
    lines = [f"🛠 <b>دعم {esc(tenant.brand_name)}</b>\n"]
    for t in tickets:
        lines.append(
            f"#{t.id} · {_STATUS.get(t.status, '?')} · {esc((t.subject or '')[:40])}"
        )
    if not tickets:
        lines.append("لا تذاكر بعد — افتح تذكرة وسنرد عليك قريباً.")
    rows = [
        [InlineKeyboardButton(text="✉️ تذكرة جديدة", callback_data="t:ticket:new")]
    ]
    for t in tickets:
        rows.append(
            [InlineKeyboardButton(text=f"عرض #{t.id}", callback_data=f"t:ticket:{t.id}")]
        )
    rows.append([InlineKeyboardButton(text="🔙 الرئيسية", callback_data="t:home")])
    await callback.answer()
    try:
        await callback.message.edit_text(
            "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
        )
    except Exception:
        pass


@router.callback_query(F.data == "t:ticket:new")
async def tenant_ticket_new(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TenantSupportStates.waiting_message)
    await callback.message.answer("✉️ اكتب رسالتك (5 أحرف على الأقل):")
    await callback.answer()


@router.message(TenantSupportStates.waiting_message)
async def tenant_ticket_received(
    message: Message, state: FSMContext, session, db_user: User, tenant: Tenant
):
    text = (message.text or "").strip()
    if len(text) < 5:
        await message.answer("⚠️ الرسالة قصيرة جداً.")
        return
    if len(text) > 2000:
        await message.answer("⚠️ الرسالة طويلة جداً (2000 حد أقصى).")
        return
    await state.clear()
    ticket = SupportTicket(
        tenant_id=tenant.id,
        user_id=db_user.id,
        subject=text.splitlines()[0][:128],
        message=text,
        status=SupportTicketStatus.OPEN,
    )
    session.add(ticket)
    await session.commit()
    await session.refresh(ticket)
    await message.answer(
        f"✅ استلمنا تذكرتك <b>#{ticket.id}</b> — سيرد عليك فريق {esc(tenant.brand_name)} قريباً."
    )


@router.callback_query(F.data.startswith("t:ticket:"))
async def tenant_ticket_view(callback: CallbackQuery, session, db_user: User, tenant: Tenant):
    if callback.data == "t:ticket:new":
        return
    try:
        ticket_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        return
    result = await session.execute(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id,
            SupportTicket.user_id == db_user.id,
            SupportTicket.tenant_id == tenant.id,
        )
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    text = (
        f"🎫 <b>التذكرة #{ticket.id}</b>\n\n"
        f"📊 الحالة: {_STATUS.get(ticket.status, '?')}\n"
        f"📝 {esc(ticket.subject)}\n\n"
        f"<b>رسالتك:</b>\n{esc(ticket.message)}"
    )
    if ticket.admin_reply:
        text += f"\n\n<b>رد الإدارة:</b>\n{esc(ticket.admin_reply)}"
    # التصعيد للمنصة (اختياري): ينسخ التذكرة لإدارة المنصة مع السياق
    rows = [[InlineKeyboardButton(text="🔙 الدعم", callback_data="t:support")]]
    if ticket.status != SupportTicketStatus.RESOLVED:
        rows.insert(
            0,
            [InlineKeyboardButton(text="⚖️ تصعيد لإدارة المنصة", callback_data=f"t:escalate:{ticket.id}")],
        )
    await callback.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("t:escalate:"))
async def tenant_escalate(callback: CallbackQuery, session, db_user: User, tenant: Tenant, bot):
    try:
        ticket_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        return
    result = await session.execute(
        select(SupportTicket).where(
            SupportTicket.id == ticket_id,
            SupportTicket.user_id == db_user.id,
            SupportTicket.tenant_id == tenant.id,
        )
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    # نسخة لإدارة المنصة عبر بوتها الأساسي (لا تكشف شيئاً للزبون)
    try:
        from aiogram import Bot as _Bot

        from config import settings

        main_bot = _Bot(token=settings.BOT_TOKEN)
        try:
            await main_bot.send_message(
                settings.ADMIN_NOTIFY_CHAT_ID,
                "⚖️ <b>تصعيد من متجر فرعي</b>\n\n"
                f"🏪 المتجر: {esc(tenant.brand_name)} (#{tenant.id})\n"
                f"👤 الزبون: <code>{db_user.telegram_id}</code>\n"
                f"🎫 التذكرة الفرعية: #{ticket.id}\n\n"
                f"📝 {esc(ticket.subject)}\n{esc(ticket.message[:800])}",
            )
        finally:
            try:
                await main_bot.session.close()
            except Exception:
                pass
        await callback.answer("تم التصعيد — ستراجع إدارة المنصة قضيتك.", show_alert=True)
    except Exception:
        await callback.answer("تعذر التصعيد حالياً.", show_alert=True)
