"""لوحات أزرار الدعم والتذاكر."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def support_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 فتح تذكرة دعم", callback_data="support:new")
    builder.button(text="🤖 مساعدة فورية", callback_data="support:ai")
    builder.button(text="📋 تذاكري", callback_data="support:list")
    builder.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()


def support_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ إلغاء", callback_data="menu:support")
    return builder.as_markup()


def support_tickets_kb(tickets) -> InlineKeyboardMarkup:
    """أزرار تذاكر المستخدم."""
    builder = InlineKeyboardBuilder()
    for ticket in tickets:
        builder.button(
            text=f"🎫 #{ticket.id} · {ticket.subject[:24]}",
            callback_data=f"support:ticket:{ticket.id}",
        )
    builder.button(text="📝 تذكرة جديدة", callback_data="support:new")
    builder.button(text="🔙 الدعم الفني", callback_data="menu:support")
    builder.adjust(1)
    return builder.as_markup()


def support_ticket_view_kb() -> InlineKeyboardMarkup:
    """أزرار تفاصيل تذكرة المستخدم."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 كل تذاكري", callback_data="support:list")
    builder.button(text="🔙 الدعم الفني", callback_data="menu:support")
    builder.adjust(1)
    return builder.as_markup()


def admin_ticket_kb(
    ticket_id: int,
    status: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if status in ("open", "in_progress"):
        builder.button(
            text="✉️ الرد على التذكرة",
            callback_data=f"admin:ticket_reply:{ticket_id}",
        )
        builder.button(
            text="✅ حل التذكرة",
            callback_data=f"admin:ticket_resolve:{ticket_id}",
        )
    builder.button(
        text="🔙 تذاكر الدعم",
        callback_data="admin:tickets",
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_tickets_kb(tickets, page: int, total_pages: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for ticket in tickets:
        builder.button(
            text=f"#{ticket.id} {ticket.subject[:28]} · {ticket.status.value}",
            callback_data=f"admin:ticket_view:{ticket.id}",
        )
    if page > 0:
        builder.button(
            text="◀️ السابق",
            callback_data=f"admin:tickets:{page - 1}",
        )
    if page < total_pages - 1:
        builder.button(
            text="التالي ▶️",
            callback_data=f"admin:tickets:{page + 1}",
        )
    builder.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    builder.adjust(1, 2, 1)
    return builder.as_markup()
