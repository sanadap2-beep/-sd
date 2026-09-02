"""
الإضافات الثلاث الهدية — على مستوى البوت كامل، لا ميزة واحدة.

1) مركز القيادة الحي (Cockpit)
   شاشة واحدة تجمع صحة كل نظام في البوت: المزودون، المدفوعات، الميزات،
   الطلبات، الأموال المحجوزة، وآخر الأخطاء. مع أزرار تنفيذ فوري.
   الفكرة: بدل أن تفتح 12 قائمة لتفهم حالة بوتك، تنظر شاشة واحدة.

2) محرك الاسترجاع الموحّد (Unified Refund Engine)
   قبل هذا الملف كان لكل ميزة مسار استرجاع خاص: الأرقام، الرشق، السوق،
   الضمان، التأمين، التجربة. لا أحد يستطيع أن يجيب «كم استرجعنا ولماذا».
   المحرك يوحّد كل المسارات تحت سلطة واحدة بسجل تدقيق واحد، ويفحص
   الاستحقاق قبل الدفع، فلا يُسترجع شيء مرتين ولا يُدفع لمن لا يستحق.

3) الحارس الذاتي (Self-Heal Sentinel)
   يراقب معدل الأخطاء في كل نظام، وإن ارتفع فوق حد يُدخل البوت «وضعاً
   آمناً» يعطّل الميزات الخطرة تلقائياً ويُبقي الأساسية. ويعيد تفعيلها
   حين يستقر الوضع. الفكرة: البوت يحمي نفسه قبل أن يخسر مالكه أموالاً.

الثلاثة تعمل معاً: الحارس يراقب، ومركز القيادة يعرض، ومحرك الاسترجاع
يصلح.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from database.models import (
    ApiProvider,
    DepositRequest,
    DepositStatus,
    EscrowHold,
    EscrowStatus,
    FeatureEvent,
    MarketListing,
    MarketListingStatus,
    MarketTransaction,
    NumberOrder,
    OrderStatus,
    Product,
    ProductStatus,
    ProviderStatus,
    SupportTicket,
    SupportTicketStatus,
    Transaction,
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
    UserSubscription,
)
from services.balance_service import BalanceService
from services.feature_service import FeatureService
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  1) مركز القيادة الحي
# ══════════════════════════════════════════════════════════════


class CockpitService:
    """شاشة واحدة لحالة البوت كله."""

    @staticmethod
    async def snapshot(session) -> dict:
        now = datetime.utcnow()
        last_24h = now - timedelta(hours=24)

        # ── المستخدمون ──
        users = int((await session.execute(select(func.count(User.id)))).scalar_one())
        banned = int(
            (
                await session.execute(
                    select(func.count(User.id)).where(User.is_banned.is_(True))
                )
            ).scalar_one()
        )

        # ── الأموال ──
        held_balance = (
            await session.execute(select(func.coalesce(func.sum(User.balance), 0)))
        ).scalar_one()
        escrow_held = (
            await session.execute(
                select(func.coalesce(func.sum(EscrowHold.amount_usd), 0)).where(
                    EscrowHold.status == "held"
                )
            )
        ).scalar_one()
        revenue_24h = (
            await session.execute(
                select(func.coalesce(func.sum(-Transaction.amount), 0)).where(
                    Transaction.type == TransactionType.PURCHASE,
                    Transaction.amount < 0,
                    Transaction.created_at >= last_24h,
                )
            )
        ).scalar_one()

        # ── الطلبات ──
        pending_numbers = int(
            (
                await session.execute(
                    select(func.count(NumberOrder.id)).where(
                        NumberOrder.status == OrderStatus.PENDING
                    )
                )
            ).scalar_one()
        )
        active_orders = int(
            (
                await session.execute(
                    select(func.count(UnifiedOrder.id)).where(
                        UnifiedOrder.status.in_(
                            [UnifiedOrderStatus.PENDING, UnifiedOrderStatus.PROCESSING]
                        )
                    )
                )
            ).scalar_one()
        )

        # ── المزودون ──
        providers_offline = int(
            (
                await session.execute(
                    select(func.count(ProviderStatus.provider)).where(
                        ProviderStatus.is_online.is_(False)
                    )
                )
            ).scalar_one()
        )
        api_providers = int(
            (
                await session.execute(
                    select(func.count(ApiProvider.id)).where(ApiProvider.is_active.is_(True))
                )
            ).scalar_one()
        )

        # ── الميزات ──
        features = await FeatureService.snapshot()
        enabled_features = sum(1 for f in features if f["enabled"])

        # ── المعلّقات التي تحتاج قراراً ──
        pending_deposits = int(
            (
                await session.execute(
                    select(func.count(DepositRequest.id)).where(
                        DepositRequest.status == DepositStatus.PENDING
                    )
                )
            ).scalar_one()
        )
        pending_listings = int(
            (
                await session.execute(
                    select(func.count(MarketListing.id)).where(
                        MarketListing.status == MarketListingStatus.PENDING_REVIEW
                    )
                )
            ).scalar_one()
        )
        open_tickets = int(
            (
                await session.execute(
                    select(func.count(SupportTicket.id)).where(
                        SupportTicket.status.in_(
                            [SupportTicketStatus.OPEN, SupportTicketStatus.IN_PROGRESS]
                        )
                    )
                )
            ).scalar_one()
        )

        # ── الاشتراكات (MRR) ──
        active_subs = int(
            (
                await session.execute(
                    select(func.count(UserSubscription.id)).where(
                        UserSubscription.is_cancelled.is_(False),
                        UserSubscription.expires_at > now,
                    )
                )
            ).scalar_one()
        )

        # ── المنتجات ──
        active_products = int(
            (
                await session.execute(
                    select(func.count(Product.id)).where(
                        Product.status == ProductStatus.ACTIVE
                    )
                )
            ).scalar_one()
        )

        # ── درجة الخطورة العامة ──
        alerts = []
        if providers_offline > 0:
            alerts.append(f"🔴 {providers_offline} مزود أرقام غير متصل")
        if pending_deposits > 0:
            alerts.append(f"🟡 {pending_deposits} طلب شحن بانتظار موافقتك")
        if pending_listings > 0:
            alerts.append(f"🟡 {pending_listings} إعلان سوق بانتظار موافقتك")
        if open_tickets > 0:
            alerts.append(f"🟡 {open_tickets} تذكرة دعم مفتوحة")
        if Decimal(str(escrow_held)) > 0:
            alerts.append(f"🔒 {escrow_held}$ محجوزة في ضمان")

        return {
            "users": users,
            "banned": banned,
            "held_balance_usd": Decimal(str(held_balance or 0)),
            "escrow_held_usd": Decimal(str(escrow_held or 0)),
            "revenue_24h_usd": Decimal(str(revenue_24h or 0)),
            "pending_numbers": pending_numbers,
            "active_orders": active_orders,
            "providers_offline": providers_offline,
            "api_providers_active": api_providers,
            "features_enabled": enabled_features,
            "features_total": len(features),
            "pending_deposits": pending_deposits,
            "pending_listings": pending_listings,
            "open_tickets": open_tickets,
            "active_subscriptions": active_subs,
            "active_products": active_products,
            "alerts": alerts,
            "generated_at": now.isoformat(),
        }

    @staticmethod
    def render(data: dict) -> str:
        """نص جاهز لرسالة تليجرام."""
        lines = [
            "🎛 <b>مركز قيادة البوت</b>",
            "",
            "👥 <b>المستخدمون</b>",
            f"   الإجمالي: {data['users']} · محظورون: {data['banned']}",
            "",
            "💰 <b>الأموال</b>",
            f"   إيراد 24س: <b>{data['revenue_24h_usd']}$</b>",
            f"   أرصدة المستخدمين: {data['held_balance_usd']}$",
            f"   محجوز في ضمان: {data['escrow_held_usd']}$",
            "",
            "📦 <b>الطلبات</b>",
            f"   أرقام معلّقة: {data['pending_numbers']}",
            f"   طلبات نشطة: {data['active_orders']}",
            f"   منتجات فعّالة: {data['active_products']}",
            f"   اشتراكات نشطة: {data['active_subscriptions']}",
            "",
            "🔌 <b>المزودون</b>",
            f"   أرقام غير متصلة: {data['providers_offline']}",
            f"   مزودو API نشطون: {data['api_providers_active']}",
            "",
            "🧩 <b>الإضافات</b>",
            f"   مفعّلة: {data['features_enabled']}/{data['features_total']}",
        ]
        if data["alerts"]:
            lines.append("")
            lines.append("⚠️ <b>يحتاج انتباهك</b>")
            lines.extend(f"   {a}" for a in data["alerts"])
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  2) محرك الاسترجاع الموحّد
# ══════════════════════════════════════════════════════════════


class RefundError(Exception):
    pass


class UnifiedRefundService:
    """
    سلطة استرجاع واحدة لكل البوت.

    القاعدتان اللتان تمنعان الخسارة:
    1) لا استرجاع بلا استحقاق مثبت (طلب موجود، وباسم هذا المستخدم).
    2) لا استرجاع مرتين — كل عملية تسجَّل بمفتاح idempotent.

    كل استرجاع يُسجَّل في جدول موحّد، فأول مرة يصير ممكناً الإجابة عن
    «كم استرجعنا هذا الشهر ولماذا».
    """

    REASONS = {
        "no_code": "لم يصل الكود",
        "provider_failure": "فشل المزود",
        "wrong_service": "خدمة خاطئة",
        "duplicate": "طلب مكرر",
        "marketplace_dispute": "نزاع في السوق",
        "insurance_claim": "مطالبة تأمين",
        "admin_goodwill": "مبادرة من الإدارة",
    }

    @staticmethod
    async def refund_number_order(
        session, order_id: int, user_id: int, reason: str, admin_id: int | None = None
    ) -> dict:
        """استرجاع طلب رقم."""
        order = await session.get(NumberOrder, order_id)
        if order is None:
            raise RefundError("الطلب غير موجود.")
        if admin_id is None and order.user_id != user_id:
            raise RefundError("هذا الطلب ليس لك.")
        if order.status == OrderStatus.REFUNDED:
            raise RefundError("سبق استرجاع هذا الطلب.")

        amount = Decimal(str(order.price_sell_usd or 0))
        if amount <= 0:
            raise RefundError("لا مبلغ لاسترجاعه.")

        await BalanceService.add_balance(
            session,
            order.user_id,
            amount,
            TransactionType.REFUND,
            description=f"استرجاع موحّد (رقم): {UnifiedRefundService.REASONS.get(reason, reason)}",
            related_table="number_orders",
            related_id=order.id,
            payment_reference=f"unified_refund:number:{order.id}",
        )
        order.status = OrderStatus.REFUNDED
        await session.commit()

        record = await UnifiedRefundService._log(
            session,
            user_id=order.user_id,
            source="number_order",
            source_id=order.id,
            amount_usd=amount,
            reason=reason,
            admin_id=admin_id,
        )
        return {"refund_id": record, "amount_usd": amount}

    @staticmethod
    async def refund_unified_order(
        session, order_id: int, user_id: int, reason: str, admin_id: int | None = None
    ) -> dict:
        """استرجاع طلب رشق/ألعاب/تطبيقات."""
        order = await session.get(UnifiedOrder, order_id)
        if order is None:
            raise RefundError("الطلب غير موجود.")
        if admin_id is None and order.user_id != user_id:
            raise RefundError("هذا الطلب ليس لك.")
        if order.status == UnifiedOrderStatus.REFUNDED:
            raise RefundError("سبق استرجاع هذا الطلب.")

        # في الجزئي نرجع قيمة ما لم يُنفَّذ فقط
        amount = Decimal(str(order.price_usd or 0))
        if order.status == UnifiedOrderStatus.PARTIAL and order.remains:
            quantity = int(order.quantity or 0)
            if quantity > 0:
                per_unit = amount / Decimal(quantity)
                amount = (per_unit * Decimal(int(order.remains))).quantize(Decimal("0.0001"))
        if amount <= 0:
            raise RefundError("لا مبلغ لاسترجاعه.")

        await BalanceService.add_balance(
            session,
            order.user_id,
            amount,
            TransactionType.REFUND,
            description=f"استرجاع موحّد: {UnifiedRefundService.REASONS.get(reason, reason)}",
            related_table="unified_orders",
            related_id=order.id,
            payment_reference=f"unified_refund:order:{order.id}",
        )
        order.status = UnifiedOrderStatus.REFUNDED
        await session.commit()

        record = await UnifiedRefundService._log(
            session,
            user_id=order.user_id,
            source="unified_order",
            source_id=order.id,
            amount_usd=amount,
            reason=reason,
            admin_id=admin_id,
        )
        return {"refund_id": record, "amount_usd": amount}

    @staticmethod
    async def _log(
        session,
        user_id: int,
        source: str,
        source_id: int,
        amount_usd: Decimal,
        reason: str,
        admin_id: int | None,
    ) -> int:
        from database.models import UnifiedRefund

        record = UnifiedRefund(
            user_id=user_id,
            source=source[:32],
            source_id=source_id,
            amount_usd=amount_usd,
            reason=reason[:32],
            admin_id=admin_id,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        await FeatureService.track("unified_refund", reason, user_id=user_id, value=str(amount_usd))
        return record.id

    @staticmethod
    async def report(session, days: int = 30) -> dict:
        """كم استرجعنا ولماذا — تقرير ما كان ممكناً قبل توحيد المسارات."""
        from database.models import UnifiedRefund

        since = datetime.utcnow() - timedelta(days=max(1, days))
        result = await session.execute(
            select(UnifiedRefund).where(UnifiedRefund.created_at >= since)
        )
        refunds = list(result.scalars().all())
        total = sum((r.amount_usd for r in refunds), Decimal("0"))
        by_reason: dict[str, dict] = {}
        for refund in refunds:
            row = by_reason.setdefault(
                refund.reason, {"count": 0, "amount_usd": Decimal("0")}
            )
            row["count"] += 1
            row["amount_usd"] += refund.amount_usd
        return {
            "period_days": days,
            "count": len(refunds),
            "total_usd": total,
            "by_reason": [
                {
                    "reason": UnifiedRefundService.REASONS.get(key, key),
                    "count": value["count"],
                    "amount_usd": value["amount_usd"],
                }
                for key, value in sorted(
                    by_reason.items(), key=lambda kv: kv[1]["amount_usd"], reverse=True
                )
            ],
        }


# ══════════════════════════════════════════════════════════════
#  3) الحارس الذاتي
# ══════════════════════════════════════════════════════════════


class SentinelService:
    """
    يراقب معدل الأخطاء ويدخل وضعاً آمناً قبل أن تتفاقم الخسارة.

    الفكرة: أغلب الكوارث تبدأ بارتفاع بطيء في الأخطاء لا يلاحظه أحد
    حتى يشتكي العملاء. الحارس يلاحظه أولاً.

    الميزات الخطرة (التي تحرّك أموالاً أو تشتري من مزودين) تُعطَّل
    تلقائياً، وتبقى الأساسية (الرصيد، الحساب، الدعم) شغّالة.
    """

    # ميزات تُعطَّل في الوضع الآمن لأنها تحرّك أموالاً أو تعتمد مزودين
    RISKY_FEATURES = (
        "peer_marketplace",
        "escrow_engine",
        "bulk_numbers",
        "autonomous_purchase_agent",
        "drip_feed",
        "warm_pool",
        "p2p_code_market",
        "revenue_sharing_tokens",
        "provider_bidding",
    )

    SAFE_MODE_KEY = "sentinel_safe_mode"
    ERROR_COUNT_KEY = "sentinel_error_count"
    LAST_CHECK_KEY = "sentinel_last_check"

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("self_heal_sentinel")

    @staticmethod
    async def error_threshold() -> int:
        return max(1, await FeatureService.config_int("self_heal_sentinel", "error_threshold", 20))

    @staticmethod
    async def window_minutes() -> int:
        return max(1, await FeatureService.config_int("self_heal_sentinel", "window_minutes", 10))

    @staticmethod
    async def is_safe_mode() -> bool:
        return await SettingsService.get_bool(SentinelService.SAFE_MODE_KEY, False)

    # مفتاح العدّاد العام، يُزاد مع كل خطأ بغض النظر عن مصدره
    GLOBAL_SOURCE = "_all"

    @staticmethod
    async def record_error(session, source: str) -> None:
        """
        يسجل خطأً. يُنادى من أي مكان يفشل.

        يُزاد عدّادان: واحد للمصدر (للتشخيص) وواحد عام (للقرار)،
        لأن الحارس يقرر على المجموع لا على مصدر واحد.
        """
        if not await SentinelService.enabled():
            return
        stamp = f"{datetime.utcnow():%Y%m%d%H%M}"
        for key_source in (source, SentinelService.GLOBAL_SOURCE):
            key = f"{SentinelService.ERROR_COUNT_KEY}:{key_source}:{stamp}"
            current = await SettingsService.get_int(key, 0)
            await SettingsService.set(session, key, str(current + 1))

    @staticmethod
    async def error_rate(session, source: str | None = None) -> int:
        """عدد الأخطاء في النافذة الحالية، للمصدر المحدد أو للجميع."""
        window = await SentinelService.window_minutes()
        now = datetime.utcnow()
        key_source = source or SentinelService.GLOBAL_SOURCE
        total = 0
        for offset in range(window):
            bucket_time = now - timedelta(minutes=offset)
            key = f"{SentinelService.ERROR_COUNT_KEY}:{key_source}:{bucket_time:%Y%m%d%H%M}"
            total += await SettingsService.get_int(key, 0)
        return total

    @staticmethod
    async def engage_safe_mode(session, reason: str) -> list[str]:
        """يعطّل الميزات الخطرة ويُبقي الأساسية."""
        disabled = []
        for key in SentinelService.RISKY_FEATURES:
            if await FeatureService.enabled(key):
                await FeatureService.set_enabled(session, key, False)
                disabled.append(key)
        await SettingsService.set(session, SentinelService.SAFE_MODE_KEY, "true")
        await SettingsService.set(session, "sentinel_safe_mode_reason", reason[:255])
        await SettingsService.set(
            session, SentinelService.LAST_CHECK_KEY, datetime.utcnow().isoformat()
        )
        if disabled:
            logger.warning("دخل البوت الوضع الآمن: عُطّلت %s ميزة. السبب: %s", len(disabled), reason)
        return disabled

    @staticmethod
    async def disengage_safe_mode(session) -> list[str]:
        """يعيد تفعيل الميزات الخطرة بعد استقرار الوضع."""
        restored = []
        for key in SentinelService.RISKY_FEATURES:
            if not await FeatureService.enabled(key):
                await FeatureService.set_enabled(session, key, True)
                restored.append(key)
        await SettingsService.set(session, SentinelService.SAFE_MODE_KEY, "false")
        await SettingsService.set(session, "sentinel_safe_mode_reason", "")
        if restored:
            logger.info("خرج البوت من الوضع الآمن: أُعيدت %s ميزة.", len(restored))
        return restored

    @staticmethod
    async def check(session) -> dict:
        """
        دورة الحارس. تدخل الوضع الآمن عند تجاوز العتبة، وتخرج منه
        حين يهدأ المعدل.
        """
        if not await SentinelService.enabled():
            return {"action": "disabled"}

        threshold = await SentinelService.error_threshold()
        rate = await SentinelService.error_rate(session)
        safe = await SentinelService.is_safe_mode()

        if not safe and rate >= threshold:
            disabled = await SentinelService.engage_safe_mode(
                session, f"معدل أخطاء {rate} تجاوز العتبة {threshold}"
            )
            return {"action": "engaged", "error_rate": rate, "disabled": disabled}

        # الخروج يتطلب هدوءاً فعلياً، لا مجرد نزول تحت العتبة
        recover_at = max(1, threshold // 4)
        if safe and rate <= recover_at:
            restored = await SentinelService.disengage_safe_mode(session)
            return {"action": "disengaged", "error_rate": rate, "restored": restored}

        return {
            "action": "holding",
            "error_rate": rate,
            "threshold": threshold,
            "safe_mode": safe,
        }

    @staticmethod
    async def status(session) -> dict:
        safe = await SentinelService.is_safe_mode()
        return {
            "enabled": await SentinelService.enabled(),
            "safe_mode": safe,
            "reason": await SettingsService.get("sentinel_safe_mode_reason", ""),
            "error_rate": await SentinelService.error_rate(session),
            "threshold": await SentinelService.error_threshold(),
            "window_minutes": await SentinelService.window_minutes(),
            "risky_features": list(SentinelService.RISKY_FEATURES),
            "currently_disabled": [
                key
                for key in SentinelService.RISKY_FEATURES
                if not await FeatureService.enabled(key)
            ],
        }
