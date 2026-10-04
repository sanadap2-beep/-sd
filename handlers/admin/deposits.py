"""مركز إدارة طلبات الشحن من داخل لوحة الأدمن."""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload

from database.models import (
    AutoInvoice,
    AutoInvoiceStatus,
    DepositRequest,
    DepositStatus,
    TransactionType,
    User,
)
from filters.admin_filter import IsAdmin
from keyboards.admin import (
    admin_deposit_view_kb,
    admin_deposits_kb,
)
from services.balance_service import BalanceService
from services.notification_service import NotificationService

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
    review_count = (
        await session.execute(
            select(func.count(AutoInvoice.id)).where(
                AutoInvoice.status == AutoInvoiceStatus.REVIEW
            )
        )
    ).scalar_one()
    kb = admin_deposits_kb(deposits, page, total_pages)
    if review_count:
        rows = list(kb.inline_keyboard)
        rows.insert(
            0,
            [
                InlineKeyboardButton(
                    text=f"🔍 فواتير تلقائية للمراجعة ({review_count})",
                    callback_data="admin:invoice_review:0",
                )
            ],
        )
        kb = InlineKeyboardMarkup(inline_keyboard=rows)
    await callback.message.edit_text(text, reply_markup=kb)


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


@router.callback_query(F.data.startswith("admin:invoice_review:"))
async def invoice_review_list(callback: CallbackQuery, session):
    try:
        page = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        page = 0
    await callback.answer()
    total = (
        await session.execute(
            select(func.count(AutoInvoice.id)).where(
                AutoInvoice.status == AutoInvoiceStatus.REVIEW
            )
        )
    ).scalar_one()
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    result = await session.execute(
        select(AutoInvoice)
        .options(selectinload(AutoInvoice.user))
        .where(AutoInvoice.status == AutoInvoiceStatus.REVIEW)
        .order_by(desc(AutoInvoice.created_at))
        .limit(_PAGE_SIZE)
        .offset(page * _PAGE_SIZE)
    )
    invoices = list(result.scalars().all())
    rows = []
    for inv in invoices:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"#{inv.id} متوقع {inv.amount_original} ← فعلي {inv.actual_amount or '؟'}",
                    callback_data=f"admin:invoice_view:{inv.id}",
                )
            ]
        )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"admin:invoice_review:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"admin:invoice_review:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="🔙 طلبات الشحن", callback_data="admin:deposits")])
    await callback.message.edit_text(
        f"🔍 <b>فواتير تلقائية تحت المراجعة</b> ({total})\n\nاختر فاتورة للاعتماد أو الرفض.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.startswith("admin:invoice_view:"))
async def invoice_review_view(callback: CallbackQuery, session):
    inv_id = int(callback.data.split(":")[2])
    inv = await session.get(AutoInvoice, inv_id)
    if inv is None:
        await callback.answer("غير موجودة.", show_alert=True)
        return
    user = await session.get(User, inv.user_id)
    credit = _proportional_credit(inv)
    text = (
        f"🔍 <b>فاتورة تلقائية #{inv.id}</b>\n\n"
        f"👤 المستخدم: <code>{user.telegram_id if user else '—'}</code>\n"
        f"💵 المتوقع: <b>{inv.amount_original} {inv.currency}</b> (≈{inv.amount_usd}$)\n"
        f"💰 المستلم فعلياً: <b>{inv.actual_amount or '—'} {inv.actual_currency or ''}</b>\n"
        f"✅ عند الاعتماد يُضاف: <b>{credit}$</b>\n"
        f"📊 الحالة: {getattr(inv.status, 'value', inv.status)}"
    )
    rows = []
    if inv.status == AutoInvoiceStatus.REVIEW:
        rows.append(
            [
                InlineKeyboardButton(text="✅ اعتماد وإضافة", callback_data=f"admin:invoice_ok:{inv.id}"),
                InlineKeyboardButton(text="❌ رفض", callback_data=f"admin:invoice_no:{inv.id}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="🔙 المراجعة", callback_data="admin:invoice_review:0")])
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


def _proportional_credit(inv: AutoInvoice) -> Decimal:
    """رصيد الاعتماد: تناسبي مع المدفوع الفعلي (عادل للزيادة والنقص)."""
    try:
        expected = Decimal(str(inv.amount_original))
        actual = Decimal(str(inv.actual_amount))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(inv.amount_usd))
    if expected <= 0 or actual <= 0:
        return Decimal(str(inv.amount_usd))
    return (Decimal(str(inv.amount_usd)) * actual / expected).quantize(Decimal("0.0001"))


@router.callback_query(F.data.startswith("admin:invoice_ok:"))
async def invoice_review_approve(callback: CallbackQuery, session, bot):
    inv = await session.get(AutoInvoice, int(callback.data.split(":")[2]))
    if inv is None or inv.status != AutoInvoiceStatus.REVIEW:
        await callback.answer("غير صالحة.", show_alert=True)
        return
    credit = _proportional_credit(inv)
    try:
        user = await BalanceService.add_balance(
            session,
            inv.user_id,
            credit,
            TransactionType.DEPOSIT,
            description=f"شحن تلقائي بعد مراجعة #{inv.id}",
            related_table="auto_invoices",
            related_id=inv.id,
            payment_reference=f"invoice:{inv.id}",
        )
    except Exception:
        await callback.answer("تعذرت الإضافة (ربما اعتُمدت مسبقاً).", show_alert=True)
        return
    inv.status = AutoInvoiceStatus.PAID
    inv.paid_at = datetime.utcnow()
    await session.commit()
    try:
        await NotificationService(bot).notify_user(
            user.telegram_id,
            f"✅ <b>تم اعتماد دفعتك!</b>\n\n💰 أُضيف <b>{credit}$</b> لرصيدك.",
        )
    except Exception:
        pass
    await callback.answer("✅ اعتُمدت وأُضيف الرصيد.")
    callback.data = f"admin:invoice_view:{inv.id}"
    await invoice_review_view(callback, session)


@router.callback_query(F.data.startswith("admin:invoice_no:"))
async def invoice_review_reject(callback: CallbackQuery, session, bot):
    inv = await session.get(AutoInvoice, int(callback.data.split(":")[2]))
    if inv is None or inv.status != AutoInvoiceStatus.REVIEW:
        await callback.answer("غير صالحة.", show_alert=True)
        return
    inv.status = AutoInvoiceStatus.FAILED
    await session.commit()
    try:
        user = await session.get(User, inv.user_id)
        if user:
            await NotificationService(bot).notify_user(
                user.telegram_id,
                f"❌ <b>رُفضت الفاتورة #{inv.id}</b>\n\nتواصل مع الدعم للمراجعة.",
            )
    except Exception:
        pass
    await callback.answer("رُفضت.")
    callback.data = f"admin:invoice_view:{inv.id}"
    await invoice_review_view(callback, session)


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
