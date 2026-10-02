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
import time
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
    async def packages() -> list[dict]:
        """قائمة الباقات: [{"days": int, "price_usd": Decimal}]

        ملاحظة: يجب أن يبقى هذا الاستدعاء ``await`` — في نسخة سابقة كان
        ``config_json`` يُستدعى بلا await فترجع coroutine وتُرمى
        TypeError عند رسم شاشة القسم لكل مستخدم بلا اشتراك.
        """
        raw = await FeatureService.config_json(
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
            sub = WaSubscription(
                user_id=user.id,
                # خيار «auto_renew_default» في إعدادات الميزة يحكم البداية
                # (كان يُقرأ أبداً في السابق، فصار إعداداً ميتاً).
                auto_renew=await FeatureService.config_bool(
                    "whatsapp_section", "auto_renew_default", True
                ),
            )
            session.add(sub)
            await session.flush()
        return sub

    # ── إعدادات الواجهة (من مركز الميزات: feat_opts ← whatsapp_section) ──

    @staticmethod
    async def menu_page_size() -> int:
        size = await FeatureService.config_int(
            "whatsapp_section", "menu_page_size", 12
        )
        return max(1, min(size, 40))

    @staticmethod
    async def menu_cache_ttl_minutes() -> int:
        ttl = await FeatureService.config_int(
            "whatsapp_section", "menu_cache_ttl_minutes", 30
        )
        return max(1, min(ttl, 24 * 60))

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
        """يرسل الرقم للجسر ويرجع كود الربط.

        عند فشل الجسر تُسترجع الحالة السابقة (لا يُترك المستخدم عالقاً في
        «بانتظار الربط» بلا سبب).
        """
        sub = await WhatsAppSectionService._get_or_create(session, user)
        previous_phone, previous_state = sub.phone, sub.link_state
        sub.phone = phone
        sub.link_state = WaLinkState.PENDING.value
        sub.link_error = None
        sub.updated_at = datetime.utcnow()
        await session.commit()
        try:
            result = await wa_bridge_client.start_link(user.telegram_id, phone)
        except wa_bridge_client.WaBridgeError as exc:
            sub.phone, sub.link_state = previous_phone, previous_state
            sub.link_error = str(exc)[:300]
            await session.commit()
            raise
        await FeatureService.track(
            "whatsapp_section", "link_start", user_id=user.id, value=phone[-4:]
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
            sub.link_error = None
        elif state == "expired":
            sub.link_state = WaLinkState.EXPIRED.value
        elif state == "none":
            sub.link_state = WaLinkState.NONE.value
        else:
            sub.link_state = WaLinkState.PENDING.value
        if status.get("connected_since"):
            sub.connected_since = status["connected_since"]
        sub.updated_at = datetime.utcnow()
        await session.commit()
        await session.refresh(sub)
        return sub

    # ── عمليات لوحة الأدمن ──

    @staticmethod
    async def extend(session, sub: WaSubscription, days: int) -> WaSubscription:
        """تمديد مجاني من الأدمن (بلا خصم) — لمشكلة مزوّد أو تعويض."""
        now = datetime.utcnow()
        base = sub.active_until if (sub.active_until and sub.active_until > now) else now
        sub.active_until = base + timedelta(days=max(1, min(int(days), 365)))
        sub.expire_notified_at = None
        sub.updated_at = now
        await session.commit()
        await session.refresh(sub)
        return sub

    @staticmethod
    async def unlink(session, sub: WaSubscription, user: User | None = None) -> bool:
        """يلغي الربط محلياً ويبلّغ الجسر (أفضل جهد) بإنهاء الجلسة."""
        sub.link_state = WaLinkState.NONE.value
        sub.link_error = None
        sub.updated_at = datetime.utcnow()
        await session.commit()
        pushed = False
        if user is not None:
            try:
                pushed = await wa_bridge_client.unlink(user.telegram_id)
            except wa_bridge_client.WaBridgeError as exc:
                logger.info("الجسر لم يؤكد إلغاء الربط: %s", exc)
        await FeatureService.track(
            "whatsapp_section", "admin_unlink", user_id=sub.user_id
        )
        return pushed

    @staticmethod
    async def last_purchase_transaction(session, user_id: int):
        """آخر عملية شراء باقة واتساب (لاستردادها من اللوحة)."""
        from database.models import Transaction

        result = await session.execute(
            select(Transaction)
            .where(
                Transaction.user_id == user_id,
                Transaction.type == TransactionType.WA_SUBSCRIPTION,
                Transaction.description.notlike("تجديد%"),
            )
            .order_by(Transaction.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def refund_purchase(session, user: User, sub: WaSubscription) -> Decimal | None:
        """يسترد آخر عملية شراء باقة (مرة واحدة) — للأدمن فقط.

        منع التكرار عبر ``payment_reference`` الفريد (نفس الآلية المستخدمة في
        الدفع التلقائي): استرداد نفس العملية مرتين مستحيل على مستوى قاعدة البيانات.
        """
        from database.models import Transaction

        tx = await WhatsAppSectionService.last_purchase_transaction(session, user.id)
        if tx is None:
            return None
        marker = f"wa-refund:{tx.id}"
        already = await session.scalar(
            select(Transaction.id).where(Transaction.payment_reference == marker)
        )
        if already:
            return None
        amount = abs(Decimal(str(tx.amount)))
        await BalanceService.add_balance(
            session,
            user.id,
            amount,
            TransactionType.REFUND,
            description=f"استرداد اشتراك واتساب — عملية #{tx.id}",
            related_table="wa_subscriptions",
            related_id=sub.id,
            payment_reference=marker,
        )
        # الاسترداد يُسقط المدة التي دفعتها هذه العملية.
        if sub.active_until:
            sub.active_until = max(
                datetime.utcnow(),
                sub.active_until - timedelta(days=sub.last_package_days or 1),
            )
        sub.updated_at = datetime.utcnow()
        await session.commit()
        await FeatureService.track(
            "whatsapp_section", "refund", user_id=user.id, value=str(amount)
        )
        return amount

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

    # ── صحة الجسر (مراقبة الأدمن) ──

    # حالة آخر إنذار حتى لا نرسل تنبيهاً كل 10 دقائق أثناء العطل.
    _bridge_down_alerted = False

    @staticmethod
    async def bridge_health(bot=None) -> dict:
        """يفحص الجسر ويبلّغ الأدمن عند الانقطاع والاستعادة.

        يُستدعى من مهمة دورية (كل 10 دقائق). لا يرسل شيئاً لو لم تُضبط
        القيم أصلاً — تلك حالة «قبل التفعيل» وليست عطلاً.
        """
        from services import wa_bridge_client

        result = {"configured": False, "ok": True, "alerted": False, "recovered": False}
        if not await wa_bridge_client.configured():
            return result
        result["configured"] = True
        info = await wa_bridge_client.probe()
        result["ok"] = bool(info.get("ok"))
        threshold = await FeatureService.config_int(
            "whatsapp_section", "bridge_alert_failures", 3
        )
        failures = wa_bridge_client.consecutive_failures()
        cls = WhatsAppSectionService

        if not result["ok"] and failures >= max(1, threshold) and not cls._bridge_down_alerted:
            cls._bridge_down_alerted = True
            await WhatsAppSectionService._notify_admins(
                bot,
                "🟠 <b>جسر واتساب لا يستجيب</b>\n\n"
                f"عدد الإخفاقات المتتالية: {failures}\n"
                f"آخر خطأ: <code>{(wa_bridge_client.last_bridge_error() or info.get('error') or '')[:200]}</code>\n\n"
                "راجع البوت الثاني: أن خدمة <code>wa_bridge/bridge.py</code> تعمل، "
                "والعنوان/السر في 🔌 الجسر مطابقان. المستخدمون سيرون رسالة خطأ عند "
                "فتح قائمة واتساب.",
                dedupe_key=f"wa-bridge-down:{int(time.time()) // 3600}",
            )
            result["alerted"] = True
        elif result["ok"] and cls._bridge_down_alerted:
            cls._bridge_down_alerted = False
            await WhatsAppSectionService._notify_admins(
                bot,
                "🟢 <b>جسر واتساب عاد</b> — اختصار الاستجابة "
                f"{info.get('latency_ms')}ms، وإصدار {info.get('version') or '1'}.",
                dedupe_key="wa-bridge-back",
            )
            result["recovered"] = True
        return result

    @staticmethod
    async def _notify_admins(bot, text: str, dedupe_key: str | None = None) -> None:
        if bot is None:
            logger.info("جسر واتساب: %s", text[:120])
            return
        try:
            from services.notification_service import NotificationService

            await NotificationService(bot).notify_admin(
                text, notification_type="system", dedupe_key=dedupe_key
            )
        except Exception:  # noqa: BLE001 — المراقبة لا تُسقط المهمة الدورية
            logger.exception("فشل إشعار الأدمن بحالة جسر واتساب")

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
