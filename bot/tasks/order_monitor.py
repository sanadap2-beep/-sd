"""
مهام الخلفية (Background Jobs).
1) check_pending_orders: فحص طلبات الأرقام المعلّقة.
2) update_provider_status: تحديث رصيد وحالة المزودين.
3) cleanup_balance_locks: تنظيف أقفال الرصيد.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal

from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    NumberOrder,
    OrderStatus,
    TransactionType,
    User,
    ProviderStatus,
    ProviderName,
)
from providers.manager import provider_manager
from providers.countries import get_number_service_by_code
from services.balance_service import BalanceService
from services.notification_service import NotificationService
from services.settings_service import SettingsService
from services.sms_receiver_service import SMSReceiverService
from services.cashback_service import CashbackService
from services.gamification_service import GamificationService
from services.loyalty_service import LoyaltyService
from keyboards.numbers import code_received_kb

logger = logging.getLogger(__name__)


async def _batch_size() -> int:
    """حجم الدفعة المتوازية — يضبطها الأدمن من مركز الإضافات."""
    from services.feature_service import FeatureService

    if not await FeatureService.enabled("instant_delivery"):
        return 1
    return max(1, await FeatureService.config_int("instant_delivery", "batch_size", 25))


async def check_pending_orders(bot):
    """
    يفحص طلبات الأرقام المعلّقة.

    كان المرور على الطلبات تسلسلياً: كل طلب ينتظر رحلة شبكة كاملة قبل
    التالي، فمع 500 طلب متزامن آخر مستخدم ينتظر دقائق. الآن تُجلب
    الحالات على دفعات متوازية، وتبقى عمليات قاعدة البيانات تسلسلية
    لأن الجلسة الواحدة غير آمنة للاستخدام المتزامن.
    """
    notifier = NotificationService(bot)
    async with async_session_maker() as session:
        result = await session.execute(
            select(NumberOrder).where(NumberOrder.status == OrderStatus.PENDING)
        )
        orders = list(result.scalars().all())
        if not orders:
            return

        # ── 1) المنتهية تُعالج أولاً ولا حاجة لاستعلام مزود عنها ──
        live: list[NumberOrder] = []
        for order in orders:
            try:
                if order.expires_at and datetime.utcnow() > order.expires_at:
                    if order.awaiting_extra_code:
                        order.status = OrderStatus.COMPLETED
                        order.awaiting_extra_code = False
                        await session.commit()
                        continue
                    await _expire_and_refund(session, order, notifier, bot)
                    continue
                live.append(order)
            except Exception as e:  # noqa: BLE001
                logger.error(f"خطأ معالجة انتهاء الطلب {order.id}: {e}")

        if not live:
            return

        # ── 2) جلب الحالات على دفعات متوازية ──
        batch_size = await _batch_size()
        statuses: dict[int, object] = {}

        async def _probe(order):
            try:
                return order.id, await SMSReceiverService.check(
                    order.provider, order.provider_order_id
                )
            except Exception as e:  # noqa: BLE001 - طلب واحد لا يُسقط الدورة
                logger.error(f"خطأ فحص الطلب {order.id}: {e}")
                return order.id, None

        for start in range(0, len(live), batch_size):
            chunk = live[start : start + batch_size]
            for order_id, status in await asyncio.gather(*(_probe(o) for o in chunk)):
                if status is not None:
                    statuses[order_id] = status

        # ── 3) تطبيق النتائج تسلسلياً على الجلسة ──
        for order in live:
            status_result = statuses.get(order.id)
            if status_result is None:
                continue
            try:
                await _update_countdown(bot, order)

                if status_result.status == "code_received" and status_result.sms_code:
                    await _handle_code_received(
                        session,
                        order,
                        status_result,
                        notifier,
                        bot,
                    )
                elif status_result.status == "cancelled":
                    await _expire_and_refund(session, order, notifier, bot)
            except Exception as e:  # noqa: BLE001
                logger.error(f"خطأ فحص الطلب {order.id}: {e}")


async def _handle_code_received(session, order, status_result, notifier, bot):
    user = await session.get(User, order.user_id)

    if order.sms_code and order.awaiting_extra_code:
        existing = order.extra_codes.split(",") if order.extra_codes else []
        if status_result.sms_code not in existing:
            existing.append(status_result.sms_code)
            order.extra_codes = ",".join(existing)
            order.awaiting_extra_code = False
            order.status = OrderStatus.COMPLETED
            await session.commit()
            await notifier.notify_user(
                user.telegram_id,
                "✅ <b>وصل الكود الإضافي!</b>\n\n"
                f"📱 الرقم: <code>{order.phone_number}</code>\n"
                f"🔑 الكود: <code>{status_result.sms_code}</code>",
            )
        return

    order.status = OrderStatus.COMPLETED
    order.sms_code = status_result.sms_code
    order.full_sms_text = status_result.full_text
    order.completed_at = datetime.utcnow()
    await session.commit()

    try:
        await provider_manager.finish_order(order.provider, order.provider_order_id)
    except Exception as e:
        logger.warning(f"تعذّر إتمام الطلب {order.id} لدى المزود: {e}")

    code_text = (
        "✅ <b>وصل الكود!</b>\n\n"
        f"📱 الرقم: <code>{order.phone_number}</code>\n"
        f"🔑 الكود: <code>{status_result.sms_code}</code>\n\n"
        f"📩 النص الكامل:\n{status_result.full_text or '—'}"
    )

    if order.status_chat_id and order.status_message_id:
        try:
            await bot.edit_message_text(
                chat_id=order.status_chat_id,
                message_id=order.status_message_id,
                text=code_text,
                reply_markup=code_received_kb(order.id),
            )
        except TelegramBadRequest:
            await notifier.notify_user(user.telegram_id, code_text)
    else:
        await notifier.notify_user(user.telegram_id, code_text)

    # ── كاشباك ──
    await CashbackService.apply_cashback(
        session,
        user.id,
        order.id,
        "number_orders",
        order.price_sell_usd,
    )
    await LoyaltyService.award_purchase_points(
        session,
        user.id,
        "number_orders",
        order.id,
        order.price_sell_usd,
    )
    await GamificationService.progress_event(session, user.id, "purchase")

    # ── إشعار القناة العامة ──
    service = await get_number_service_by_code(session, order.service)
    service_name = service.name_ar if service else order.service
    await notifier.notify_successful_number_order(
        username=user.username,
        full_name=user.full_name,
        service_name=service_name,
        price_usd=str(order.price_sell_usd),
    )


async def _update_countdown(bot, order):
    if not (order.status_chat_id and order.status_message_id and order.expires_at):
        return

    remaining = order.expires_at - datetime.utcnow()
    if remaining.total_seconds() <= 0:
        return

    minutes, seconds = divmod(int(remaining.total_seconds()), 60)
    try:
        await bot.edit_message_text(
            chat_id=order.status_chat_id,
            message_id=order.status_message_id,
            text=(
                "✅ <b>تم شراء الرقم بنجاح!</b>\n\n"
                f"📱 الرقم: <code>{order.phone_number}</code>\n"
                f"⏳ بانتظار الكود... "
                f"المتبقي: {minutes}:{seconds:02d}\n\n"
                "سيتم التحديث تلقائياً."
            ),
        )
    except TelegramBadRequest:
        pass


async def _expire_and_refund(session, order, notifier, bot):
    order.status = OrderStatus.EXPIRED
    await session.commit()

    try:
        await provider_manager.cancel_order(order.provider, order.provider_order_id)
    except Exception as e:
        logger.warning(f"تعذّر إلغاء الطلب {order.id} لدى المزود: {e}")

    user = await BalanceService.add_balance(
        session,
        order.user_id,
        order.price_sell_usd,
        TransactionType.REFUND,
        description=(f"استرجاع - انتهت صلاحية الطلب #{order.id}"),
        related_table="number_orders",
        related_id=order.id,
    )
    order.status = OrderStatus.REFUNDED
    await session.commit()

    text = (
        f"⌛ <b>انتهت صلاحية الرقم</b> "
        f"<code>{order.phone_number}</code>\n"
        f"💰 تم استرجاع <b>{order.price_sell_usd}$</b> "
        f"إلى رصيدك تلقائياً."
    )

    if order.status_chat_id and order.status_message_id:
        try:
            await bot.edit_message_text(
                chat_id=order.status_chat_id,
                message_id=order.status_message_id,
                text=text,
            )
            return
        except TelegramBadRequest:
            pass
    await notifier.notify_user(user.telegram_id, text)


async def update_provider_status(bot):
    notifier = NotificationService(bot)
    threshold = await SettingsService.get_decimal("provider_low_balance_threshold", Decimal("10"))

    async with async_session_maker() as session:
        for provider in ProviderName:
            status = await session.get(ProviderStatus, provider)
            if status is None:
                continue

            try:
                balance = await provider_manager.get_balance(provider)
                status.balance = balance
                status.is_online = True
                status.last_error = None

                if balance < threshold:
                    await notifier.notify_provider_low_balance(
                        provider_name=provider.value,
                        balance=str(balance),
                        threshold=str(threshold),
                    )

            except Exception as e:
                was_online = status.is_online
                status.is_online = False
                status.last_error = str(e)[:500]

                if was_online:
                    await notifier.notify_provider_offline(
                        provider_name=provider.value,
                        error=str(e)[:200],
                    )

            status.last_checked_at = datetime.utcnow()

        await session.commit()


async def cleanup_balance_locks():
    removed = BalanceService.cleanup_idle_locks()
    if removed:
        logger.debug(f"تم تنظيف {removed} قفل رصيد غير مستخدم.")
