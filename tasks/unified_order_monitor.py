"""
مراقبة طلبات الألعاب والتطبيقات والـ SMM (UnifiedOrder).
تُفحص كل دقيقتين لأن هذه الطلبات لا تحتاج سرعة
مثل طلبات الأرقام.
"""

import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.engine import async_session_maker
from database.models import (
    UnifiedOrder,
    UnifiedOrderStatus,
    TransactionType,
)
from protocols.base import ProtocolError
from protocols.factory import ProtocolFactory
from services.balance_service import BalanceService
from services.notification_service import NotificationService
from services.dynamic_service import DynamicService
from services.gamification_service import GamificationService
from services.loyalty_service import LoyaltyService

logger = logging.getLogger(__name__)


async def check_unified_orders(bot):
    """
    يفحص كل الطلبات الموحدة المعلّقة أو قيد المعالجة.
    """
    notifier = NotificationService(bot)

    async with async_session_maker() as session:
        result = await session.execute(
            select(UnifiedOrder)
            .where(
                UnifiedOrder.status.in_(
                    [
                        UnifiedOrderStatus.PENDING,
                        UnifiedOrderStatus.PROCESSING,
                    ]
                )
            )
            .options(
                selectinload(UnifiedOrder.product),
                selectinload(UnifiedOrder.api_provider),
                selectinload(UnifiedOrder.user),
            )
        )
        orders = result.scalars().all()

        if not orders:
            return

        logger.debug(f"فحص {len(orders)} طلب موحد معلّق...")

        for order in orders:
            try:
                await _process_unified_order(session, order, notifier, bot)
            except Exception as e:
                logger.error(f"خطأ فحص الطلب الموحد {order.id}: {e}")


async def _process_unified_order(session, order: UnifiedOrder, notifier: NotificationService, bot):
    """يعالج طلباً موحداً واحداً."""

    if not order.api_provider or not order.external_order_id:
        if order.status == UnifiedOrderStatus.PENDING:
            logger.warning(f"الطلب #{order.id} بدون مزود أو رقم خارجي، يُعتبر مكتملاً يدوياً.")
        return

    provider = order.api_provider
    if not provider.is_active:
        return

    try:
        protocol = ProtocolFactory.create_from_provider(provider)
        status_result = await protocol.check_order_status(order.external_order_id)
        status_data = {
            "status": status_result.status,
            "charge": status_result.charge,
            "remains": status_result.remains,
            "start_count": status_result.start_count,
            "raw": status_result.raw,
        }
    except ProtocolError as e:
        logger.warning(f"فشل فحص الطلب #{order.id} من المزود: {e}")
        provider.last_error = str(e)[:500]
        await session.commit()
        return
    except Exception as e:
        logger.error(f"خطأ غير متوقع فحص الطلب #{order.id}: {e}")
        return

    new_status = status_data.get("status", "pending")
    start_count = status_data.get("start_count")
    remains = status_data.get("remains")

    if start_count is not None:
        order.start_count = start_count
    if remains is not None:
        order.remains = remains

    order.result_data = json.dumps(
        status_data.get("raw", {}),
        ensure_ascii=False,
    )

    user = order.user
    product_name = order.product.name_ar if order.product else "خدمة"

    if new_status == "completed":
        await _handle_completed(session, order, user, product_name, notifier)

    elif new_status == "partial":
        await _handle_partial(
            session,
            order,
            user,
            product_name,
            notifier,
            remains,
        )

    elif new_status == "failed":
        await _handle_failed(session, order, user, product_name, notifier)

    elif new_status == "processing":
        if order.status != UnifiedOrderStatus.PROCESSING:
            order.status = UnifiedOrderStatus.PROCESSING
            order.status_message = "قيد التنفيذ"
            await session.commit()
            await notifier.notify_user(
                user.telegram_id,
                f"🔄 <b>طلبك قيد التنفيذ</b>\n\n"
                f"🛒 المنتج: {product_name}\n"
                f"🆔 رقم الطلب: #{order.id}\n"
                "سيصلك إشعار عند الاكتمال.",
            )

    await session.commit()


async def _handle_completed(session, order, user, product_name, notifier):
    """يعالج الطلب المكتمل."""
    was_completed = order.status == UnifiedOrderStatus.COMPLETED
    order.status = UnifiedOrderStatus.COMPLETED
    order.status_message = "مكتمل"
    order.completed_at = datetime.utcnow()
    await session.commit()

    if not was_completed:
        await notifier.live_purchase_success(
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name,
            item=product_name,
            amount_usd=str(order.price_usd),
            order_id=order.id,
        )

    if order.product:
        await DynamicService.increment_product_sold(session, order.product_id)

    await LoyaltyService.award_purchase_points(
        session,
        user.id,
        "unified_orders",
        order.id,
        order.price_usd,
    )
    await GamificationService.progress_event(session, user.id, "purchase")

    extra = ""
    try:
        raw = json.loads(order.result_data or "{}")
        if isinstance(raw, dict):
            nested = raw.get("data") if isinstance(raw.get("data"), dict) else {}
            code = (
                nested.get("code")
                or nested.get("sms")
                or nested.get("sms_code")
                or raw.get("code")
                or raw.get("sms")
            )
            phone = nested.get("phone") or nested.get("number") or raw.get("phone")
            if phone:
                extra += f"\n📞 الرقم: <code>{phone}</code>"
            if code:
                extra += f"\n🔑 الكود: <code>{code}</code>"
    except Exception:
        extra = ""
    await notifier.notify_order_completed(
        user_telegram_id=user.telegram_id,
        product_name=product_name,
        result_text=(
            f"✅ تم تنفيذ طلبك بنجاح!\n🆔 رقم الطلب: #{order.id}{extra}"
        ),
    )

    await notifier.notify_successful_unified_order(
        username=user.username,
        full_name=user.full_name,
        product_name=product_name,
        price_usd=str(order.price_usd),
    )

    logger.info(f"الطلب الموحد #{order.id} اكتمل بنجاح.")


async def _handle_partial(session, order, user, product_name, notifier, remains):
    """
    يعالج الطلب الجزئي.
    يسترجع الفرق بين ما طُلب وما نُفِّذ.
    """
    order.status = UnifiedOrderStatus.PARTIAL
    order.status_message = f"جزئي - متبقي: {remains}"
    order.completed_at = datetime.utcnow()

    if remains and order.quantity > 0 and order.price_usd > 0:
        from decimal import Decimal

        price_per_unit = order.price_usd / Decimal(str(order.quantity))
        refund_amount = price_per_unit * Decimal(str(remains))

        if refund_amount > 0:
            await BalanceService.add_balance(
                session,
                user.id,
                refund_amount,
                TransactionType.REFUND,
                description=(f"استرجاع جزئي - طلب #{order.id} ({remains} متبقي)"),
                related_table="unified_orders",
                related_id=order.id,
            )

            await notifier.live_refund(
                telegram_id=user.telegram_id,
                username=user.username,
                full_name=user.full_name,
                item=product_name,
                amount_usd=str(refund_amount),
                reason="الطلب منجز جزئياً — رُجع المتبقي غير المنفَّذ",
                order_id=order.id,
            )

            await notifier.notify_user(
                user.telegram_id,
                f"⚠️ <b>طلب منجز جزئياً</b>\n\n"
                f"🛒 المنتج: {product_name}\n"
                f"🆔 رقم الطلب: #{order.id}\n"
                f"📊 المتبقي غير منجز: {remains}\n"
                f"💰 تم استرجاع <b>{refund_amount:.4f}$</b> "
                f"لرصيدك.",
            )
    else:
        await notifier.notify_user(
            user.telegram_id,
            f"⚠️ <b>طلب منجز جزئياً</b>\n\n"
            f"🛒 المنتج: {product_name}\n"
            f"🆔 رقم الطلب: #{order.id}\n"
            "تواصل مع الدعم الفني للمزيد من التفاصيل.",
        )

    await session.commit()
    logger.info(f"الطلب الموحد #{order.id} منجز جزئياً.")


async def _handle_failed(session, order, user, product_name, notifier):
    """يعالج الطلب الفاشل ويسترجع الرصيد."""
    order.status = UnifiedOrderStatus.FAILED
    order.status_message = "فشل التنفيذ"
    order.completed_at = datetime.utcnow()
    await session.commit()

    await BalanceService.add_balance(
        session,
        user.id,
        order.price_usd,
        TransactionType.REFUND,
        description=(f"استرجاع - فشل تنفيذ الطلب #{order.id}"),
        related_table="unified_orders",
        related_id=order.id,
    )
    order.status = UnifiedOrderStatus.REFUNDED
    await session.commit()

    await notifier.live_refund(
        telegram_id=user.telegram_id,
        username=user.username,
        full_name=user.full_name,
        item=product_name,
        amount_usd=str(order.price_usd),
        reason="فشل تنفيذ الطلب لدى المزود",
        order_id=order.id,
    )

    await notifier.notify_order_failed(
        user_telegram_id=user.telegram_id,
        product_name=product_name,
        amount_usd=str(order.price_usd),
    )

    logger.info(f"الطلب الموحد #{order.id} فشل وتم استرجاع {order.price_usd}$.")
