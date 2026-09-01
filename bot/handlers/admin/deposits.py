"""مركز إدارة طلبات الشحن من داخل لوحة الأدمن."""

from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload

from database.models import DepositRequest, DepositStatus
from filters.admin_filter import IsAdmin
from keyboards.admin import (
    admin_deposit_view_kb,
    admin_deposits_kb,
)

router = Router(name="admin_deposits")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_PAGE_SIZE = 10


async def _render_deposits(callback: CallbackQuery, session, page: int = 0):
    total = (await session.execute(select(func.count(DepositRequest.id)))).scalar_one()
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    result = await session.execute(
        select(DepositRequest)
        .options(selectinload(DepositRequest.user))
        .order_by(desc(DepositRequest.created_at))
        .limit(_PAGE_SIZE)
        .offset(page * _PAGE_SIZE)
    )
    deposits = list(result.scalars().all())
    pending = (
        await session.execute(
            select(func.count(DepositRequest.id)).where(
                DepositRequest.status == DepositStatus.PENDING
            )
        )
    ).scalar_one()
    text = (
        "💳 <b>طلبات الشحن</b>\n\n"
        f"⏳ المعلقة: <b>{pending}</b>\n"
        f"📋 الإجمالي: {total}\n"
        f"📄 الصفحة: {page + 1}/{total_pages}\n\n"
        "اختر طلباً للمراجعة."
    )
    await callback.message.edit_text(
        text,
        reply_markup=admin_deposits_kb(deposits, page, total_pages),
    )


@router.callback_query(F.data == "admin:deposits")
async def deposits_list(callback: CallbackQuery, session):
    await callback.answer()
    await _render_deposits(callback, session)


@router.callback_query(F.data.startswith("admin:deposits:"))
async def deposits_page(callback: CallbackQuery, session):
    try:
        page = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        page = 0
    await callback.answer()
    await _render_deposits(callback, session, page)


@router.callback_query(F.data.startswith("admin:deposit_view:"))
async def deposit_view(callback: CallbackQuery, session):
    deposit_id = int(callback.data.split(":")[2])
    result = await session.execute(
        select(DepositRequest)
        .options(selectinload(DepositRequest.user))
        .where(DepositRequest.id == deposit_id)
    )
    deposit = result.scalar_one_or_none()
    if deposit is None:
        await callback.answer("⚠️ طلب الشحن غير موجود.", show_alert=True)
        return
    user = deposit.user
    status = getattr(deposit.status, "value", str(deposit.status))
    text = (
        f"💳 <b>طلب الشحن #{deposit.id}</b>\n\n"
        f"👤 المستخدم: {escape(user.full_name if user else '—')}\n"
        f"🆔 Telegram ID: <code>{user.telegram_id if user else '—'}</code>\n"
        f"💰 المبلغ: <b>{deposit.amount_usd}$</b>\n"
        f"💳 الطريقة: {escape(deposit.payment_method or '—')}\n"
        f"🔢 رقم العملية: <code>{escape(deposit.proof_tx_number or '—')}</code>\n"
        f"📊 الحالة: <b>{status}</b>\n"
        f"📅 التاريخ: {deposit.created_at.strftime('%Y-%m-%d %H:%M')}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=admin_deposit_view_kb(
            deposit.id,
            deposit.status == DepositStatus.PENDING,
        ),
    )
    await callback.answer()
