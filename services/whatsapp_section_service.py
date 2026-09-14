"""
خدمة قسم واتساب (البوت الثاني عبر الجسر).

- الباقات: يوم / 3 / 7 / 30 يوم (أسعار من إعدادات الميزة، قابلة للتعديل
  من لوحة الأدمن) + تجديد تلقائي يومي بسعر اليوم إن كان الرصيد كافياً.
- الربط: المستخدم يرسل رقمه → الجسر يفتح جلسة ويرجع كود ربط →
  المستخدم يكمل عند مزوده → نفحص الحالة.
- كل أزرار البوت الثاني تُجلب من الجسر (GET /menu) وتُنفذ (POST /action)
  باسم المستخدم نفسه — فلا حاجة لإعادة كتابة أي أمر هنا.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from database.models import TransactionType, User, WaLinkState, WaSubscription
from services.balance_service import BalanceService, InsufficientBalanceError
from services.feature_service import FeatureService
from services import wa_bridge_client

logger = logging.getLogger(__name__)


class WaSectionError(Exception):
    pass


class WhatsAppSectionService:
    """الاشتراكات والباقات."""

    @staticmethod
    def packages() -> list[dict]:
        """قائمة الباقات: [{"days": int, "price_usd": float}]"""
        raw = FeatureService.config_json(
            "whatsapp_section",
            "packages_json",
            [
                {"days": 1, "price_usd": 1.0},
                {"days": 3, "price_usd": 2.85},
                {"days": 7, "price_usd": 6.30},
                {"days": 30, "price_usd": 25.50},
            ],
        )
        out = []
        for item in raw:
            try:
                days = int(item.get("days"))
                price = Decimal(str(item.get("price_usd")))
                if days > 0 and price > 0:
                    out.append({"days": days, "price_usd": price})
            except (AttributeError, TypeError, ValueError):
                continue
        out.sort(key=lambda p: p["days"])
        return out or [{"days": 1, "price_usd": Decimal("1.0")}]

    @staticmethod
    async def daily_price() -> Decimal:
        return Decimal(
            str(await FeatureService.config_decimal("whatsapp_section", "price_per_day_usd", 1.0))
        )

    @staticmethod
    async def get_sub(session, user_id: int) -> WaSubscription | None:
        result = await session.execute(
            select(WaSubscription).where(WaSubscription.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _get_or_create(session, user: User) -> WaSubscription:
        sub = await WhatsAppSectionService.get_sub(session, user.id)
        if sub is None:
            sub = WaSubscription(user_id=user.id)
            session.add(sub)
            await session.flush()
        return sub

    @staticmethod
    def is_active(sub: WaSubscription | None) -> bool:
        return bool(sub and sub.active_until and sub.active_until > datetime.utcnow())

    @staticmethod
    async def purchase(
        session, user: User, days: int, price: Decimal
    ) -> WaSubscription:
        """يباع باقة: خصم من الرصيد + تمدد الصلاحية.

        إذا كان فيه اشتراك نشط، تتمدد من نهايته (تراكب)، وإلا من الآن.
        """
        sub = await WhatsAppSectionService._get_or_create(session, user)
        await BalanceService.deduct_balance(
            session,
            user.id,
            price,
            TransactionType.WA_SUBSCRIPTION,
            description=f"اشتراك واتساب {days} يوم",
            related_table="wa_subscriptions",
            related_id=sub.id,
        )
        now = datetime.utcnow()
        base = sub.active_until if (sub.active_until and sub.active_until > now) else now
        sub.active_until = base + timedelta(days=days)
        sub.last_package_days = days
        sub.expire_notified_at = None
        sub.updated_at = now
        await session.commit()
        await session.refresh(sub)
        await FeatureService.track(
            "whatsapp_section", "purchase", user_id=user.id, value=str(days)
        )
        return sub

    # ── ربط جلسة الواتساب ──

    @staticmethod
    async def start_link(session, user: User, phone: str) -> dict:
        """يرسل الرقم للجسر ويرجع كود الربط."""
        sub = await WhatsAppSectionService._get_or_create(session, user)
        sub.phone = phone
        sub.link_state = WaLinkState.PENDING.value
        sub.updated_at = datetime.utcnow()
        await session.commit()
        result = await wa_bridge_client.start_link(user.telegram_id, phone)
        await FeatureService.track(
            "whatsapp_section", "link_start", user_id=user.id
        )
        return result

    @staticmethod
    async def check_link(session, user: User) -> WaSubscription:
        """يفحص حالة الربط عند الجسر ويحدّثه محلياً."""
        sub = await WhatsAppSectionService._get_or_create(session, user)
        status = await wa_bridge_client.link_status(user.telegram_id)
        state = status.get("state")
        if state == "linked":
            sub.link_state = WaLinkState.LINKED.value
        elif state == "expired":
            sub.link_state = WaLinkState.EXPIRED.value
        elif state == "none":
            sub.link_state = WaLinkState.NONE.value
        else:
            sub.link_state = WaLinkState.PENDING.value
        sub.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(sub)
        return sub

    # ── الدور الدوري: تجديد تلقائي + تنبيه انتهاء ──

    @staticmethod
    async def renewal_cycle(session, bot=None) -> dict:
        """
        يُنفذ كل ساعة:
        - اشتراك ينتهي خلال reminder_hours + auto_renew + رصيد كافٍ →
          خصم سعر اليوم وتمديد 24 ساعة (تجديد سلس بلا انقطاع).
        - اشتراك انتهى → تنبيه واحد «انتهى اشتراكك».
        """
        stats = {"renewed": 0, "expired_notified": 0, "skipped_balance": 0}
        if not await FeatureService.enabled("whatsapp_section"):
            return stats

        reminder_hours = await FeatureService.config_int(
            "whatsapp_section", "reminder_hours_before", 12
        )
        daily = await WhatsAppSectionService.daily_price()
        now = datetime.utcnow()
        threshold = now + timedelta(hours=reminder_hours)

        result = await session.execute(
            select(WaSubscription).where(WaSubscription.active_until.is_not(None))
        )
        for sub in result.scalars().all():
            if sub.active_until is None:
                continue

            if sub.active_until <= now:
                # منتهي: تنبيه مرة واحدة.
                if sub.expire_notified_at is None and bot is not None:
                    try:
                        user = await session.get(User, sub.user_id)
                        if user:
                            from services.i18n_service import I18nService

                            lang = user.language_code or "ar"
                            await bot.send_message(
                                user.telegram_id,
                                I18nService.t("wa_expired_notice", lang),
                            )
                    except Exception:  # noqa: BLE001
                        logger.exception("فشل تنبيه انتهاء واتساب للمستخدم %s", sub.user_id)
                    sub.expire_notified_at = now
                    stats["expired_notified"] += 1
                await session.commit()
                continue

            # سينتهي قريباً → تجديد تلقائي.
            if sub.active_until > threshold or not sub.auto_renew:
                continue
            try:
                await BalanceService.deduct_balance(
                    session,
                    sub.user_id,
                    daily,
                    TransactionType.WA_SUBSCRIPTION,
                    description="تجديد اشتراك واتساب (يوم)",
                    related_table="wa_subscriptions",
                    related_id=sub.id,
                )
            except InsufficientBalanceError:
                stats["skipped_balance"] += 1
                continue
            sub.active_until = sub.active_until + timedelta(days=1)
            sub.last_renewed_at = now
            sub.expire_notified_at = None
            sub.updated_at = now
            stats["renewed"] += 1
            if bot is not None:
                try:
                    user = await session.get(User, sub.user_id)
                    if user:
                        from services.i18n_service import I18nService

                        lang = user.language_code or "ar"
                        await bot.send_message(
                            user.telegram_id,
                            I18nService.t("wa_renewed_notice", lang, price=f"{daily:g}$"),
                        )
                except Exception:  # noqa: BLE001
                    logger.exception("فشل تنبيه تجديد واتساب للمستخدم %s", sub.user_id)
            await session.commit()

        if stats["renewed"] or stats["expired_notified"]:
            logger.info("دورة واتساب: %s", stats)
        return stats

    # ── إحصاءات ──

    @staticmethod
    async def stats(session) -> dict:
        active_count = (
            await session.scalar(
                select(func.count(WaSubscription.id)).where(
                    WaSubscription.active_until > datetime.utcnow()
                )
            )
            or 0
        )
        linked_count = (
            await session.scalar(
                select(func.count(WaSubscription.id)).where(
                    WaSubscription.link_state == WaLinkState.LINKED.value
                )
            )
            or 0
        )
        from database.models import Transaction

        since = datetime.utcnow() - timedelta(days=30)
        revenue_30d = (
            await session.scalar(
                select(
                    func.coalesce(func.sum(func.abs(Transaction.amount)), 0)
                ).where(
                    Transaction.type == TransactionType.WA_SUBSCRIPTION,
                    Transaction.created_at >= since,
                )
            )
            or 0
        )
        return {
            "active": int(active_count),
            "linked": int(linked_count),
            "revenue_30d": Decimal(str(revenue_30d)),
        }
