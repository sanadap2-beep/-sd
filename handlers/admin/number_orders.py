"""إدارة طلبات أرقام استقبال SMS للأدمن."""

from datetime import datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload

from database.models import NumberOrder, OrderStatus, TransactionType
from filters.admin_filter import IsAdmin
from keyboards.admin import (
    admin_number_order_detail_kb,
    admin_number_order_refund_confirm_kb,
    admin_number_orders_kb,
)
from providers.manager import provider_manager
from services.balance_service import BalanceService
from services.notification_service import NotificationService

router = Router(name="admin_number_orders")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_PAGE_SIZE = 10
_STATUS_LABELS = {
    OrderStatus.PENDING: "⏳ معلّق",
    OrderStatus.CODE_RECEIVED: "✅ وصل الكود",
    OrderStatus.COMPLETED: "✅ مكتمل",
    OrderStatus.EXPIRED: "⌛ منتهي",
    OrderStatus.CANCELLED: "❌ ملغى",
    OrderStatus.REFUNDED: "↩️ مسترجع",
}


def _status_label(status) -> str:
    return _STATUS_LABELS.get(status, getattr(status, "value", str(status)))


async def _render_number_orders(
    callback: CallbackQuery,
    session,
    page: int = 0,
):
    total = (await session.execute(select(func.count(NumberOrder.id)))).scalar_one()
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    result = await session.execute(
        select(NumberOrder)
        .options(selectinload(NumberOrder.user))
        .order_by(desc(NumberOrder.purchased_at))
        .limit(_PAGE_SIZE)
        .offset(page * _PAGE_SIZE)
    )
    orders = list(result.scalars().all())

    pending = (
        await session.execute(
            select(func.count(NumberOrder.id)).where(NumberOrder.status == OrderStatus.PENDING)
        )
    ).scalar_one()

    text = (
        "📞 <b>طلبات أرقام SMS</b>\n\n"
        f"⏳ قيد الانتظار: <b>{pending}</b>\n"
        f"📋 الإجمالي: {total}\n"
        f"📄 الصفحة: {page + 1}/{total_pages}\n\n"
        "اضغط على طلب لعرض التفاصيل."
    )
    await callback.message.edit_text(
        text,
        reply_markup=admin_number_orders_kb(orders, page, total_pages),
    )


@router.callback_query(F.data == "admin:number_orders")
async def number_orders_list(callback: CallbackQuery, session):
    await callback.answer()
    await _render_number_orders(callback, session, 0)


@router.callback_query(F.data.startswith("admin:number_orders:"))
async def number_orders_page(callback: CallbackQuery, session):
    try:
        page = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        page = 0
    await callback.answer()
    await _render_number_orders(callback, session, page)


async def _get_number_order(session, order_id: int):
    result = await session.execute(
        select(NumberOrder)
        .options(selectinload(NumberOrder.user))
        .where(NumberOrder.id == order_id)
    )
    return result.scalar_one_or_none()


async def _render_detail(callback: CallbackQuery, session, order):
    user_name = order.user.full_name if order.user else "—"
    user_id = order.user.telegram_id if order.user else "—"
    text = (
        f"📞 <b>تفاصيل طلب الرقم #{order.id}</b>\n\n"
        f"👤 المستخدم: {user_name} (<code>{user_id}</code>)\n"
        f"📱 الرقم: <code>{order.phone_number}</code>\n"
        f"📲 الخدمة: {order.service}\n"
        f"🌍 الدولة: {order.country_code}\n"
        f"🏭 المزود: {order.provider.value}\n"
        f"🆔 رقم المزود: <code>{order.provider_order_id}</code>\n"
        f"📊 الحالة: <b>{_status_label(order.status)}</b>\n"
        f"💰 سعر البيع: <b>{order.price_sell_usd}$</b>\n"
        f"💵 التكلفة: {order.price_provider_usd}$\n"
        f"📅 الشراء: {order.purchased_at.strftime('%Y-%m-%d %H:%M')}"
    )
    if order.sms_code:
        text += f"\n🔑 الكود: <code>{order.sms_code}</code>"
    if order.expires_at:
        text += f"\n⌛ الانتهاء: {order.expires_at.strftime('%Y-%m-%d %H:%M')}"

    await callback.message.edit_text(
        text,
        reply_markup=admin_number_order_detail_kb(order),
    )


@router.callback_query(F.data.startswith("admin:num_order_view:"))
async def number_order_view(callback: CallbackQuery, session):
    order_id = int(callback.data.split(":")[2])
    order = await _get_number_order(session, order_id)
    if order is None:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    await callback.answer()
    await _render_detail(callback, session, order)


@router.callback_query(F.data.startswith("admin:num_order_refund_ask:"))
async def number_order_refund_ask(callback: CallbackQuery, session):
    order_id = int(callback.data.split(":")[2])
    order = await _get_number_order(session, order_id)
    if order is None:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    if order.status != OrderStatus.PENDING:
        await callback.answer(
            "⚠️ لا يمكن استرجاع هذا الطلب بحالته الحالية.",
            show_alert=True,
        )
        return
    await callback.message.edit_text(
        f"⚠️ <b>تأكيد استرجاع طلب الرقم #{order.id}</b>\\n\\n"
        f"📱 الرقم: <code>{order.phone_number}</code>\\n"
        f"💰 سيتم استرجاع: <b>{order.price_sell_usd}$</b>\\n\\n"
        "هل أنت متأكد؟",
        reply_markup=admin_number_order_refund_confirm_kb(order.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:num_order_refund:"))
async def number_order_refund(
    callback: CallbackQuery,
    session,
    bot,
):
    order_id = int(callback.data.split(":")[2])
    order = await _get_number_order(session, order_id)
    if order is None:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return

    if order.status != OrderStatus.PENDING:
        await callback.answer(
            "⚠️ لا يمكن استرجاع هذا الطلب بحالته الحالية.",
            show_alert=True,
        )
        return

    try:
        await provider_manager.cancel_order(order.provider, order.provider_order_id)
    except Exception:
        # The provider may already have expired the order. The admin can
        # still refund the user while the failed cancellation is logged by
        # the provider layer.
        pass

    user = await BalanceService.add_balance(
        session,
        order.user_id,
        order.price_sell_usd,
        TransactionType.REFUND,
        description=f"استرجاع إداري لطلب الرقم #{order.id}",
        related_table="number_orders",
        related_id=order.id,
    )
    order.status = OrderStatus.REFUNDED
    order.completed_at = datetime.utcnow()
    await session.commit()

    await NotificationService(bot).notify_user(
        user.telegram_id,
        "↩️ <b>تم استرجاع قيمة طلب الرقم</b>\n\n"
        f"📱 الرقم: <code>{order.phone_number}</code>\n"
        f"💰 المبلغ المسترجع: <b>{order.price_sell_usd}$</b>",
    )
    await callback.answer("✅ تم الإلغاء واسترجاع الرصيد.")
    await _render_detail(callback, session, order)
