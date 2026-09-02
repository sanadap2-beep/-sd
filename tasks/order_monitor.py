"""
مهام الخلفية (Background Jobs):
1) check_pending_orders: فحص طلبات الأرقام المعلّقة واستلام الأكواد وإرسال الإشعار للقناة العامة.
2) update_provider_status: تحديث رصيد وحالة المزودين (إشعار انخفاض الرصيد كل 24 ساعة فقط).
3) cleanup_balance_locks: تنظيف أقفال الرصيد.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select

from database.engine import async_session_maker
from database.models import (
    NumberOrder,
    OrderStatus,
    ProviderName,
    ProviderStatus,
    TransactionType,
    User,
)
from providers.countries import get_country_by_code, get_number_service_by_code
from providers.manager import provider_manager
from services.balance_service import BalanceService
from services.cashback_service import CashbackService
from services.gamification_service import GamificationService
from services.loyalty_service import LoyaltyService
from services.notification_service import NotificationService
from services.settings_service import SettingsService
from services.sms_receiver_service import SMSReceiverService
from keyboards.numbers import code_received_kb

logger = logging.getLogger(__name__)


async def _batch_size() -> int:
    """حجم الدفعة المتوازية."""
    from services.feature_service import FeatureService

    if not await FeatureService.enabled("instant_delivery"):
        return 1
    return max(1, await FeatureService.config_int("instant_delivery", "batch_size", 25))


async def check_pending_orders(bot):
    """يفحص طلبات الأرقام المعلّقة."""
    notifier = NotificationService(bot)
    async with async_session_maker() as session:
        result = await session.execute(
            select(NumberOrder).where(NumberOrder.status == OrderStatus.PENDING)
        )
        orders = list(result.scalars().all())
        if not orders:
            return

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
            except Exception as e:
                logger.error(f"خطأ معالجة انتهاء الطلب {order.id}: {e}")

        if not live:
            return

        batch_size = await _batch_size()
        statuses: dict[int, object] = {}

        async def _probe(order):
            try:
                return order.id, await SMSReceiverService.check(
                    order.provider, order.provider_order_id
                )
            except Exception as e:
                logger.error(f"خطأ فحص الطلب {order.id}: {e}")
                return order.id, None

        for start in range(0, len(live), batch_size):
            chunk = live[start : start + batch_size]
            for order_id, status in await asyncio.gather(*(_probe(o) for o in chunk)):
                if status is not None:
                    statuses[order_id] = status

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
            except Exception as e:
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

    was_completed = order.status == OrderStatus.COMPLETED
    order.status = OrderStatus.COMPLETED
    order.sms_code = status_result.sms_code
    order.full_sms_text = status_result.full_text
    order.completed_at = datetime.utcnow()
    await session.commit()

    if not was_completed:
        await notifier.live_purchase_success(
            telegram_id=user.telegram_id,
            username=user.username,
            full_name=user.full_name,
            item=f"رقم <code>{order.phone_number}</code> — خدمة {order.service}",
            amount_usd=str(order.price_sell_usd),
            order_id=order.id,
        )

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

    # كاشباك وولاء
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

    # ── إرسال الإشعار بالقالب الجديد إلى القناة العامة ──
    service = await get_number_service_by_code(session, order.service)
    country = await get_country_by_code(session, order.country_code)

    await notifier.notify_successful_number_order(
        order=order,
        country=country,
        service=service,
        user=user,
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
                f"⏳ بانتظار الكود... المتبقي: {minutes}:{seconds:02d}\n\n"
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
        description=f"استرجاع - انتهت صلاحية الطلب #{order.id}",
        related_table="number_orders",
        related_id=order.id,
    )
    order.status = OrderStatus.REFUNDED
    await session.commit()

    await notifier.live_refund(
        telegram_id=user.telegram_id,
        username=user.username,
        full_name=user.full_name,
        item=f"رقم <code>{order.phone_number}</code> — خدمة {order.service}",
        amount_usd=str(order.price_sell_usd),
        reason="الرقم لم يتفعّل/انتهت صلاحيته",
        order_id=order.id,
    )

    text = (
        f"⌛ <b>انتهت صلاحية الرقم</b> <code>{order.phone_number}</code>\n"
        f"💰 تم استرجاع <b>{order.price_sell_usd}$</b> إلى رصيدك تلقائياً."
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


# ══════════════════════════════════════════════════════════════
# ══════════ تحديث حالة المزودين وفحص الرصيد كل 24 ساعة ══════════
# ══════════════════════════════════════════════════════════════


async def update_provider_status(bot):
    """
    تحديث أرصدة المزودين:
    - فحص الاتصال وتحديث الرصيد في قاعدة البيانات.
    - إرسال تنبيه انخفاض الرصيد مرة واحدة فقط كل 24 ساعة لكل مزود.
    - إعادة تعيين التنبيه تلقائياً إذا شحن الأدمن رصيد المزود.
    """
    notifier = NotificationService(bot)
    threshold = await SettingsService.get_decimal("provider_low_balance_threshold", Decimal("10"))
    now = datetime.utcnow()

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

                alert_setting_key = f"last_low_balance_alert_{provider.value}"

                if balance < threshold:
                    last_alert_str = await SettingsService.get(alert_setting_key)
                    should_alert = True

                    if last_alert_str:
                        try:
                            last_alert_time = datetime.fromisoformat(last_alert_str)
                            if now - last_alert_time < timedelta(hours=24):
                                should_alert = False
                        except Exception:
                            should_alert = True

                    if should_alert:
                        await notifier.notify_provider_low_balance(
                            provider_name=provider.value,
                            balance=str(balance),
                            threshold=str(threshold),
                        )
                        await SettingsService.set(session, alert_setting_key, now.isoformat())
                else:
                    last_alert_str = await SettingsService.get(alert_setting_key)
                    if last_alert_str:
                        await SettingsService.set(session, alert_setting_key, "")

            except Exception as e:
                was_online = status.is_online
                status.is_online = False
                status.last_error = str(e)[:500]

                if was_online:
                    await notifier.notify_provider_offline(
                        provider_name=provider.value,
                        error=str(e)[:200],
                    )

            status.last_checked_at = now

        await session.commit()


async def cleanup_balance_locks():
    removed = BalanceService.cleanup_idle_locks()
    if removed:
        logger.debug(f"تم تنظيف {removed} قفل رصيد غير مستخدم.")