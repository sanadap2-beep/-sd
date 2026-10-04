"""Tenant monitor: follow up PENDING sub-bot orders + bill subscriptions.

يعمل في عملية البوت الأساسية (وصول DB مباشر) كل دقيقتين للطلبات
ويومياً للفوترة. الإشعارات تُرسل عبر بوت كل تاجر (هوية مخفية).
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.engine import async_session_maker
from database.models import Tenant, UnifiedOrder, UnifiedOrderStatus
from protocols.factory import ProtocolFactory
from services.tenant_order_service import TenantOrderService
from services.tenant_service import TenantService

logger = logging.getLogger(__name__)


async def check_tenant_orders():
    """يفحص طلبات الفروع المعلقة ويحدثها عبر المزود (نطاق كل مستأجر)."""
    async with async_session_maker() as session:
        tenants = (
            await session.execute(
                select(Tenant).where(
                    Tenant.is_active.is_(True),
                    Tenant.subscription_status.in_(["active", "grace"]),
                )
            )
        ).scalars().all()
        tenant_ids = [t.id for t in tenants]
        if not tenant_ids:
            return
        result = await session.execute(
            select(UnifiedOrder)
            .where(
                UnifiedOrder.tenant_id.in_(tenant_ids),
                UnifiedOrder.status.in_(
                    [UnifiedOrderStatus.PENDING, UnifiedOrderStatus.PROCESSING]
                ),
            )
            .options(
                selectinload(UnifiedOrder.api_provider),
                selectinload(UnifiedOrder.user),
            )
        )
        orders = list(result.scalars().all())
    for order in orders:
        try:
            await _process_one(order.id)
        except Exception:
            logger.exception("فشل متابعة الطلب الفرعي %s", order.id)


async def _process_one(order_id: int):
    from services.tenant_runtime import get_tenant_bot_by_id

    async with async_session_maker() as session:
        order = await session.get(UnifiedOrder, order_id)
        if order is None or order.status not in (
            UnifiedOrderStatus.PENDING,
            UnifiedOrderStatus.PROCESSING,
        ):
            return
        tenant = await session.get(Tenant, order.tenant_id or 0)
        if tenant is None or not TenantService.is_usable(tenant):
            return
        if not order.api_provider or not order.external_order_id:
            return
        provider = order.api_provider
        if not provider.is_active:
            return
        try:
            protocol = ProtocolFactory.create_from_provider(provider)
            res = await protocol.check_order_status(order.external_order_id)
        except Exception as exc:
            logger.warning("تعذر فحص الطلب الفرعي %s: %s", order.id, exc)
            return
        status = str(getattr(res, "status", "")).lower()
        try:
            sub_bot = None
            try:
                from services.tenant_service import TenantService as _TS

                token = _TS.reveal_token(tenant)
                sub_bot = get_tenant_bot_by_id(tenant.id, token)
            except Exception:
                sub_bot = None
            user_tg = order.user.telegram_id if order.user else None

            if status == "completed":
                order.status = UnifiedOrderStatus.COMPLETED
                order.completed_at = datetime.utcnow()
                code = getattr(res, "sms_code", None) or getattr(res, "code", None)
                if code:
                    order.status_message = f"الكود: {code}"
                await session.commit()
                if sub_bot is not None and user_tg:
                    text = f"✅ <b>اكتمل طلبك #{order.id}!</b>"
                    if code:
                        text += f"\n\n🎁 الكود: <code>{code}</code>"
                    try:
                        await sub_bot.send_message(user_tg, text)
                    except Exception:
                        pass
            elif status in ("failed", "canceled", "cancelled", "refunded"):
                ok = await TenantOrderService.refund_order(
                    session, tenant, order.id, "فشل التنفيذ لدى المزود"
                )
                if ok and sub_bot is not None and user_tg:
                    try:
                        await sub_bot.send_message(
                            user_tg,
                            f"❌ <b>فشل الطلب #{order.id}</b> — تم استرجاع المبلغ لرصيدك.",
                        )
                    except Exception:
                        pass
            else:
                if order.status != UnifiedOrderStatus.PROCESSING:
                    order.status = UnifiedOrderStatus.PROCESSING
                    await session.commit()
        except Exception:
            logger.exception("فشل معالجة نتيجة الطلب الفرعي %s", order.id)


async def bill_tenant_subscriptions(bot=None):
    """فوترة يومية: $8 من محفظة كل مستأجر مستحق + تجميد webhook للمعلقين."""
    async with async_session_maker() as session:
        report = await TenantService.bill_due(session)
    if report.get("suspended"):
        for tenant_id in report["suspended"]:
            try:
                async with async_session_maker() as session:
                    tenant = await session.get(Tenant, tenant_id)
                    if tenant is None:
                        continue
                    await TenantService.unregister_webhook(session, tenant)
                    try:
                        from services.tenant_runtime import drop_tenant_bot

                        drop_tenant_bot(tenant_id)
                    except Exception:
                        pass
                    # إشعار مالك المتجر في البوت الأساسي
                    if bot is not None and tenant.owner_user_id:
                        from database.models import User

                        async with async_session_maker() as s2:
                            owner = await s2.get(User, tenant.owner_user_id)
                            if owner:
                                try:
                                    await bot.send_message(
                                        owner.telegram_id,
                                        "⛔ <b>توقف متجرك الفرعي</b>\n\n"
                                        f"🏪 {tenant.brand_name}\n"
                                        "السبب: انتهت مهلة الاشتراك الشهري ($8).\n"
                                        "موّل المحفظة ثم جدد من /mystore لإعادة التفعيل.",
                                    )
                                except Exception:
                                    pass
            except Exception:
                logger.exception("فشل تجميد المستأجر %s", tenant_id)
    # تنبيه السماح
    if report.get("graced") and bot is not None:
        for tenant_id in report["graced"]:
            try:
                async with async_session_maker() as session:
                    tenant = await session.get(Tenant, tenant_id)
                    if tenant is None:
                        continue
                    from database.models import User

                    owner = await session.get(User, tenant.owner_user_id)
                    if owner:
                        try:
                            await bot.send_message(
                                owner.telegram_id,
                                "⚠️ <b>تعذر خصم اشتراك متجرك ($8)</b>\n\n"
                                f"🏪 {tenant.brand_name}\n"
                                "دخلت فترة سماح 3 أيام — موّل المحفظة قبل التجميد.",
                            )
                        except Exception:
                            pass
            except Exception:
                pass
    return report
