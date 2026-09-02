"""
مدير دورة حياة الاشتراكات.

المشكلة التي يحلها هذا الملف:
`services/subscription_service.py` الموجود ليس لإدارة الاشتراكات — كله
25 سطراً عن القنوات الإجبارية (MandatoryChannel). لا يوجد في البوت أي
تتبع لانتهاء اشتراك منتج، فلا تنبيه ولا تجديد، ويضيع الإيراد المتكرر.

الحل:
- كل اشتراك يُسجَّل بتاريخ انتهائه.
- مهمة خلفية تنبّه قبل N أيام (مرة واحدة لكل مستوى تنبيه).
- تجديد تلقائي لمن فعّله، وإلغاء لمن ألغاه.
- تقرير MRR للأدمن.

قابل للإيقاف والضبط من مركز الإضافات.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from database.models import (
    Product,
    SubscriptionPlan,
    TransactionType,
    User,
    UserSubscription,
)
from services.balance_service import BalanceService, InsufficientBalanceError
from services.feature_service import FeatureService
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class SubscriptionError(Exception):
    pass


class SubscriptionLifecycleService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("subscription_lifecycle")

    @staticmethod
    async def warn_days() -> int:
        return await FeatureService.config_int("subscription_lifecycle", "warn_days_before", 3)

    # ─────────── الإنشاء ───────────

    @staticmethod
    async def create(
        session,
        user_id: int,
        product_id: int,
        days: int,
        auto_renew: bool | None = None,
        plan_id: int | None = None,
    ) -> UserSubscription:
        if not await SubscriptionLifecycleService.enabled():
            raise SubscriptionError("إدارة الاشتراكات موقوفة حالياً.")
        if days <= 0:
            raise SubscriptionError("مدة الاشتراك يجب أن تكون موجبة.")

        if auto_renew is None:
            auto_renew = await FeatureService.config_bool(
                "subscription_lifecycle", "auto_renew", True
            )

        subscription = UserSubscription(
            user_id=user_id,
            product_id=product_id,
            plan_id=plan_id,
            starts_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(days=days),
            auto_renew=auto_renew,
        )
        session.add(subscription)
        await session.commit()
        await session.refresh(subscription)
        await FeatureService.track(
            "subscription_lifecycle", "created", user_id=user_id, value=f"{days}d"
        )
        return subscription

    @staticmethod
    async def active_for(session, user_id: int) -> list[UserSubscription]:
        now = datetime.utcnow()
        result = await session.execute(
            select(UserSubscription).where(
                UserSubscription.user_id == user_id,
                UserSubscription.is_cancelled.is_(False),
                UserSubscription.expires_at > now,
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def set_auto_renew(session, subscription_id: int, user_id: int, value: bool) -> bool:
        subscription = await session.get(UserSubscription, subscription_id)
        if subscription is None or subscription.user_id != user_id:
            return False
        subscription.auto_renew = value
        await session.commit()
        return True

    @staticmethod
    async def cancel(session, subscription_id: int, user_id: int) -> bool:
        """يلغي التجديد التلقائي دون سحب المدة المدفوعة."""
        subscription = await session.get(UserSubscription, subscription_id)
        if subscription is None or subscription.user_id != user_id:
            return False
        subscription.auto_renew = False
        subscription.is_cancelled = True
        await session.commit()
        return True

    # ─────────── دورة الخلفية ───────────

    @staticmethod
    async def run_cycle(session, bot=None) -> dict:
        """ينبّه المنتهية قريباً ويجدد لمن فعّل التجديد التلقائي."""
        stats = {"reminded": 0, "renewed": 0, "renew_failed": 0, "expired": 0}
        if not await SubscriptionLifecycleService.enabled():
            return stats

        now = datetime.utcnow()
        warn_days = await SubscriptionLifecycleService.warn_days()
        notifier = NotificationService(bot) if bot is not None else None

        result = await session.execute(
            select(UserSubscription).where(
                UserSubscription.is_cancelled.is_(False),
                UserSubscription.expires_at > now,
            )
        )
        for subscription in result.scalars().all():
            days_left = (subscription.expires_at - now).days

            # ── تنبيه لمرة واحدة لكل مستوى ──
            if days_left <= warn_days and subscription.last_reminded_days_left != days_left:
                subscription.last_reminded_days_left = days_left
                stats["reminded"] += 1
                if notifier is not None:
                    product = await session.get(Product, subscription.product_id)
                    try:
                        await notifier.notify_user(
                            (await session.get(User, subscription.user_id)).telegram_id,
                            "⏰ <b>اشتراكك على وشك الانتهاء</b>\n\n"
                            f"📦 {product.name_ar if product else '—'}\n"
                            f"📅 ينتهي بعد <b>{days_left}</b> يوم\n"
                            + ("🔄 التجديد التلقائي مفعّل.\n"
                               if subscription.auto_renew
                               else "فعّل التجديد التلقائي حتى لا ينقطع."),
                        )
                    except Exception:  # noqa: BLE001
                        pass

        # ── التجديد التلقائي للمنتهية ──
        expired_result = await session.execute(
            select(UserSubscription).where(
                UserSubscription.is_cancelled.is_(False),
                UserSubscription.expires_at <= now,
            )
        )
        for subscription in expired_result.scalars().all():
            if not subscription.auto_renew:
                subscription.is_cancelled = True
                stats["expired"] += 1
                continue

            plan = (
                await session.get(SubscriptionPlan, subscription.plan_id)
                if subscription.plan_id
                else None
            )
            if plan is None:
                subscription.is_cancelled = True
                stats["expired"] += 1
                continue

            try:
                await BalanceService.deduct_balance(
                    session,
                    subscription.user_id,
                    plan.price_usd,
                    TransactionType.PURCHASE,
                    description=f"تجديد اشتراك #{subscription.id}",
                    related_table="user_subscriptions",
                    related_id=subscription.id,
                    is_purchase=True,
                )
            except (InsufficientBalanceError, ValueError):
                subscription.auto_renew = False
                subscription.is_cancelled = True
                stats["renew_failed"] += 1
                if notifier is not None:
                    try:
                        user = await session.get(User, subscription.user_id)
                        await notifier.notify_user(
                            user.telegram_id,
                            "❌ <b>تعذّر تجديد اشتراكك</b>\n\n"
                            "رصيدك غير كافٍ. أشحن رصيدك وأعد الاشتراك يدوياً.",
                        )
                    except Exception:  # noqa: BLE001
                        pass
                continue

            days = max(1, plan.days)
            base = max(subscription.expires_at, now)
            subscription.expires_at = base + timedelta(days=days)
            subscription.last_reminded_days_left = None
            stats["renewed"] += 1

        await session.commit()
        if any(stats.values()):
            logger.info(
                "دورة الاشتراكات: نبّه %s، جدّد %s، فشل %s، انتهى %s",
                stats["reminded"], stats["renewed"], stats["renew_failed"], stats["expired"],
            )
        return stats

    # ─────────── التقرير ───────────

    @staticmethod
    async def mrr_report(session) -> dict:
        """الإيراد الشهري المتكرر المتوقع من الاشتراكات النشطة."""
        now = datetime.utcnow()
        horizon = now + timedelta(days=30)
        result = await session.execute(
            select(UserSubscription).where(
                UserSubscription.is_cancelled.is_(False),
                UserSubscription.expires_at > now,
            )
        )
        subscriptions = list(result.scalars().all())
        active = len(subscriptions)
        auto = sum(1 for s in subscriptions if s.auto_renew)
        renewing_soon = sum(
            1 for s in subscriptions if s.expires_at <= horizon
        )
        total_value = Decimal("0")
        for subscription in subscriptions:
            plan = (
                await session.get(SubscriptionPlan, subscription.plan_id)
                if subscription.plan_id
                else None
            )
            if plan is not None:
                total_value += plan.price_usd
        return {
            "active_subscriptions": active,
            "auto_renew": auto,
            "expiring_within_30d": renewing_soon,
            "expected_monthly_usd": total_value,
        }
