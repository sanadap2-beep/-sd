"""إدارة الطلبات الموحدة من لوحة الأدمن.

تسمح للأدمن بمتابعة الطلبات المعلقة، إكمال الطلبات اليدوية، أو استرجاع
الرصيد بأمان عند فشل مزود خارجي.
"""

import json
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload

from database.models import (
    UnifiedOrder,
    UnifiedOrderStatus,
    TransactionType,
)
from filters.admin_filter import IsAdmin
from states.states import AdminSubManualStates
from keyboards.admin import (
    admin_order_detail_kb,
    admin_order_refund_confirm_kb,
    admin_orders_kb,
)
from services.balance_service import BalanceService
from services.notification_service import NotificationService

router = Router(name="admin_orders")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

_PAGE_SIZE = 10
_STATUS_LABELS = {
    UnifiedOrderStatus.PENDING: "⏳ معلّق",
    UnifiedOrderStatus.PROCESSING: "🔄 قيد التنفيذ",
    UnifiedOrderStatus.COMPLETED: "✅ مكتمل",
    UnifiedOrderStatus.FAILED: "❌ فشل",
    UnifiedOrderStatus.REFUNDED: "↩️ مسترجع",
    UnifiedOrderStatus.PARTIAL: "⚠️ جزئي",
}


def _status_label(status) -> str:
    return _STATUS_LABELS.get(status, getattr(status, "value", str(status)))


async def _render_orders(callback: CallbackQuery, session, page: int = 0):
    total = (await session.execute(select(func.count(UnifiedOrder.id)))).scalar_one()
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    result = await session.execute(
        select(UnifiedOrder)
        .options(
            selectinload(UnifiedOrder.product),
            selectinload(UnifiedOrder.user),
        )
        .order_by(desc(UnifiedOrder.created_at))
        .limit(_PAGE_SIZE)
        .offset(page * _PAGE_SIZE)
    )
    orders = list(result.scalars().all())

    pending = (
        await session.execute(
            select(func.count(UnifiedOrder.id)).where(
                UnifiedOrder.status.in_(
                    [
                        UnifiedOrderStatus.PENDING,
                        UnifiedOrderStatus.PROCESSING,
                    ]
                )
            )
        )
    ).scalar_one()

    text = (
        "📦 <b>إدارة الطلبات</b>\n\n"
        f"⏳ قيد المتابعة: <b>{pending}</b>\n"
        f"📋 إجمالي الطلبات: {total}\n"
        f"📄 الصفحة: {page + 1}/{total_pages}\n\n"
    )
    if not orders:
        text += "لا توجد طلبات بعد."
    else:
        text += "اضغط على طلب لعرض التفاصيل وإدارته."

    await callback.message.edit_text(
        text,
        reply_markup=admin_orders_kb(orders, page, total_pages),
    )


@router.callback_query(F.data == "admin:orders")
async def orders_list(callback: CallbackQuery, session):
    await callback.answer()
    await _render_orders(callback, session, 0)


@router.callback_query(F.data.startswith("admin:orders:"))
async def orders_page(callback: CallbackQuery, session):
    try:
        page = int(callback.data.split(":")[2])
    except (ValueError, IndexError):
        page = 0
    await callback.answer()
    await _render_orders(callback, session, page)


async def _get_order(session, order_id: int):
    result = await session.execute(
        select(UnifiedOrder)
        .options(
            selectinload(UnifiedOrder.product),
            selectinload(UnifiedOrder.user),
            selectinload(UnifiedOrder.api_provider),
        )
        .where(UnifiedOrder.id == order_id)
    )
    return result.scalar_one_or_none()


async def _render_order_detail(callback: CallbackQuery, session, order):
    product_name = order.product.name_ar if order.product else "—"
    user_name = order.user.full_name if order.user else "—"
    user_id = order.user.telegram_id if order.user else "—"
    provider_name = order.api_provider.name if order.api_provider else "يدوي/غير محدد"

    text = (
        f"📦 <b>تفاصيل الطلب #{order.id}</b>\n\n"
        f"👤 المستخدم: {user_name} (<code>{user_id}</code>)\n"
        f"🛒 المنتج: <b>{product_name}</b>\n"
        f"📊 الحالة: <b>{_status_label(order.status)}</b>\n"
        f"💰 سعر البيع: <b>{order.price_usd}$</b>\n"
        f"💵 التكلفة: {order.cost_price_usd}$\n"
        f"🔌 المزود: {provider_name}\n"
        f"🎯 الهدف: <code>{order.target or '—'}</code>\n"
        f"📊 الكمية: {order.quantity}\n"
        f"🆔 رقم المزود: <code>{order.external_order_id or '—'}</code>\n"
        f"📝 الملاحظة: {order.status_message or '—'}\n"
        f"📅 الإنشاء: {order.created_at.strftime('%Y-%m-%d %H:%M')}"
    )
    if order.remains is not None:
        text += f"\n⏳ المتبقي: {order.remains}"

    await callback.message.edit_text(
        text,
        reply_markup=admin_order_detail_kb(order),
    )


@router.callback_query(F.data.startswith("admin:order_view:"))
async def order_view(callback: CallbackQuery, session):
    order_id = int(callback.data.split(":")[2])
    order = await _get_order(session, order_id)
    if order is None:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    await callback.answer()
    await _render_order_detail(callback, session, order)


@router.callback_query(F.data.startswith("admin:order_complete:"))
async def order_complete(callback: CallbackQuery, session, bot):
    order_id = int(callback.data.split(":")[2])
    order = await _get_order(session, order_id)
    if order is None:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return

    if order.status not in (
        UnifiedOrderStatus.PENDING,
        UnifiedOrderStatus.PROCESSING,
    ):
        await callback.answer(
            "⚠️ لا يمكن إكمال هذا الطلب بحالته الحالية.",
            show_alert=True,
        )
        return

    order.status = UnifiedOrderStatus.COMPLETED
    order.status_message = "تم الإكمال يدوياً من الإدارة"
    order.completed_at = datetime.utcnow()
    await session.commit()

    if order.user:
        await NotificationService(bot).notify_order_completed(
            order.user.telegram_id,
            order.product.name_ar if order.product else "خدمة",
            f"🆔 رقم الطلب: #{order.id}\nتم تأكيد التنفيذ من الإدارة.",
        )
    await callback.answer("✅ تم تعليم الطلب كمكتمل.")
    await _render_order_detail(callback, session, order)


@router.callback_query(F.data.startswith("admin:order_refund_ask:"))
async def order_refund_ask(callback: CallbackQuery, session):
    order_id = int(callback.data.split(":")[2])
    order = await _get_order(session, order_id)
    if order is None:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    if order.status not in (
        UnifiedOrderStatus.PENDING,
        UnifiedOrderStatus.PROCESSING,
        UnifiedOrderStatus.PARTIAL,
        UnifiedOrderStatus.FAILED,
    ):
        await callback.answer(
            "⚠️ لا يمكن استرجاع هذا الطلب بحالته الحالية.",
            show_alert=True,
        )
        return
    await callback.message.edit_text(
        f"⚠️ <b>تأكيد استرجاع الطلب #{order.id}</b>\\n\\n"
        f"📦 المنتج: {order.product.name_ar if order.product else '—'}\\n"
        f"💰 سيتم استرجاع: <b>{order.price_usd}$</b>\\n\\n"
        "هذا الإجراء لا يمكن التراجع عنه. هل أنت متأكد؟",
        reply_markup=admin_order_refund_confirm_kb(order.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:order_refund:"))
async def order_refund(callback: CallbackQuery, session, bot):
    order_id = int(callback.data.split(":")[2])
    order = await _get_order(session, order_id)
    if order is None:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return

    if order.status in (
        UnifiedOrderStatus.COMPLETED,
        UnifiedOrderStatus.REFUNDED,
    ):
        await callback.answer(
            "⚠️ الطلب مكتمل أو تم استرجاعه مسبقاً.",
            show_alert=True,
        )
        return

    user = await BalanceService.add_balance(
        session,
        order.user_id,
        order.price_usd,
        TransactionType.REFUND,
        description=f"استرجاع إداري للطلب الموحد #{order.id}",
        related_table="unified_orders",
        related_id=order.id,
    )
    order.status = UnifiedOrderStatus.REFUNDED
    order.status_message = "تم الاسترجاع من الإدارة"
    order.completed_at = datetime.utcnow()
    await session.commit()

    await NotificationService(bot).notify_user(
        user.telegram_id,
        "↩️ <b>تم استرجاع قيمة طلبك</b>\n\n"
        f"🆔 الطلب: #{order.id}\n"
        f"💰 المبلغ المسترجع: <b>{order.price_usd}$</b>",
    )
    await callback.answer("✅ تم استرجاع الرصيد.")
    await _render_order_detail(callback, session, order)

@router.callback_query(F.data.startswith("admin:sub_send:"))
async def sub_send_start(callback: CallbackQuery, session, state: FSMContext):
    """بدء التسليم اليدوي لاشتراك رقمي بعد نفاد رصيد المزود."""
    order_id = int(callback.data.split(":")[2])
    order = await _get_order(session, order_id)
    if order is None:
        await callback.answer("⚠️ الطلب غير موجود.", show_alert=True)
        return
    if order.status not in (UnifiedOrderStatus.PENDING, UnifiedOrderStatus.PROCESSING):
        await callback.answer(
            "⚠️ لا يمكن التسليم بهذا الطلب بحالته الحالية.",
            show_alert=True,
        )
        return
    await state.update_data(order_id=order_id)
    await state.set_state(AdminSubManualStates.waiting_data)
    await callback.answer()
    user = order.user
    user_line = (
        f"{user.telegram_id} (@{user.username or '-'})" if user else "—"
    )
    await callback.message.edit_text(
        "📤 <b>تسليم اشتراك رقمي يدوياً</b>\n\n"
        f"🆔 الطلب: <b>#{order.id}</b>\n"
        f"👤 المستخدم: {user_line}\n\n"
        "أرسل بيانات الحساب/الكود/الرابط في رسالة نصية واحدة.\n"
        "أرسل <code>-</code> للإلغاء."
    )


@router.message(AdminSubManualStates.waiting_data)
async def sub_send_data(message: Message, session, bot, state: FSMContext):
    data = await state.get_data()
    order_id = int(data.get("order_id", 0))
    await state.clear()
    raw = (message.text or message.caption or "").strip()
    if raw in {"", "-", "إلغاء"}:
        await message.answer("تم إلغاء التسليم اليدوي.")
        return
    order = await _get_order(session, order_id)
    if order is None:
        await message.answer("⚠️ الطلب غير موجود.")
        return
    if order.status not in (UnifiedOrderStatus.PENDING, UnifiedOrderStatus.PROCESSING):
        await message.answer("⚠️ الطلب بحالة لا تسمح بالتسليم.")
        return
    from services.digital_delivery import format_delivery_html

    order.status = UnifiedOrderStatus.COMPLETED
    order.status_message = "تم التسليم يدوياً من الإدارة (اشتراك رقمي)"
    order.completed_at = datetime.utcnow()
    order.result_data = json.dumps({"content": raw}, ensure_ascii=False)
    await session.commit()

    if order.user:
        delivery_html = format_delivery_html({"content": raw})
        await NotificationService(bot).notify_user(
            order.user.telegram_id,
            f"🎁 <b>وصلك اشتراكك!</b>\n\n"
            f"🆔 الطلب: #{order.id}\n"
            f"📦 {order.product.name_ar if order.product else 'خدمة'}\n\n"
            f"{delivery_html or '<code>' + raw + '</code>'}"
        )
    await message.answer("✅ تم التسليم وإشعار المستخدم.")
