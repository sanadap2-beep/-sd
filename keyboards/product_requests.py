"""أزرار طلبات السوق والتصويت."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def product_requests_kb(requests) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for request in requests:
        builder.button(
            text=f"👍 {request.votes_count} · {request.title[:34]}",
            callback_data=f"market:request:view:{request.id}", style="success",
        )
    builder.button(text="➕ اطلب خدمة جديدة", callback_data="market:request:new", style="success")
    builder.button(text="📋 طلباتي", callback_data="market:request:mine", style="success")
    builder.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()


def product_request_detail_kb(request_id: int, can_vote: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_vote:
        builder.button(
            text="👍 أؤيد توفير هذه الخدمة",
            callback_data=f"market:request:vote:{request_id}", style="success",
        )
    builder.button(text="📣 كل الطلبات", callback_data="market:requests", style="success")
    builder.button(text="🔙 القائمة الرئيسية", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()


def admin_product_requests_kb(requests) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for request in requests:
        builder.button(
            text=f"👍 {request.votes_count} · #{request.id} {request.title[:25]}",
            callback_data=f"admin:market_request:view:{request.id}",
            style="success",
        )
    builder.button(text="🔙 لوحة الإدارة", callback_data="admin:main")
    builder.adjust(1)
    return builder.as_markup()


def admin_product_request_detail_kb(request_id: int, status: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if status in ("open", "in_review"):
        builder.button(
            text="🔎 قيد الدراسة",
            callback_data=f"admin:market_request:review:{request_id}",
            style="success",
        )
        builder.button(
            text="✅ تم توفير الخدمة",
            callback_data=f"admin:market_request:fulfill:{request_id}",
            style="success",
        )
        builder.button(
            text="❌ رفض الطلب",
            callback_data=f"admin:market_request:reject:{request_id}", style="danger",
        )
    builder.button(text="🔙 طلبات السوق", callback_data="admin:market_requests")
    builder.adjust(1)
    return builder.as_markup()
