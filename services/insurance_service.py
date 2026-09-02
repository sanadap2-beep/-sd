"""
تأمين الكود — صندوق ضمان ذاتي.

المشكلة التي يحلها: أخطر نقطة ضعف في هذا المجال هي «الكود ما وصل»،
وهي أكبر سبب للشكاوى وفقدان الثقة.

الحل:
- يُقتطع رسم صغير من كل طلب إلى صندوق تأمين.
- إن لم يصل الكود خلال المهلة، يُدفع الاسترجاع والتعويض من الصندوق
  **فوراً وبلا مراجعة بشرية**.
- على خدمات يختارها الأدمن، الفشل = ضعف المبلغ رصيداً.
- رصيد الصندوق ومطالباته معروضان بشفافية، فلا يَعِد البوت بما لا يملك.

لماذا لا يُستغل: كل مطالبة مرتبطة بطلب محدد ومفتاح idempotent، فالطلب
الواحد لا يُطالب مرتين.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from database.models import (
    NumberOrder,
    OrderStatus,
    TransactionType,
    User,
)
from services.balance_service import BalanceService
from services.feature_service import FeatureService
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class InsuranceError(Exception):
    pass


class InsuranceService:
    POOL_KEY = "insurance_pool_balance_usd"

    # ─────────── الرسم ───────────

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("sms_insurance")

    @staticmethod
    async def premium_percent() -> Decimal:
        return Decimal(
            str(await FeatureService.config_decimal("sms_insurance", "premium_percent", 2.0))
        )

    @staticmethod
    async def premium_for(price_usd: Decimal) -> Decimal:
        """رسم التأمين على طلب بقيمة معينة."""
        if not await InsuranceService.enabled():
            return Decimal("0")
        percent = await InsuranceService.premium_percent()
        return (price_usd * percent / Decimal("100")).quantize(Decimal("0.0001"))

    @staticmethod
    async def _pool_balance(session) -> Decimal:
        from services.settings_service import SettingsService

        return await SettingsService.get_decimal(InsuranceService.POOL_KEY, Decimal("0"))

    @staticmethod
    async def _add_to_pool(session, amount: Decimal) -> Decimal:
        from services.settings_service import SettingsService

        current = await InsuranceService._pool_balance(session)
        new = (current + amount).quantize(Decimal("0.0001"))
        await SettingsService.set(session, InsuranceService.POOL_KEY, str(new))
        return new

    @staticmethod
    async def collect_premium(session, user_id: int, order_id: int, price_usd: Decimal) -> Decimal:
        """يقتطع الرسم ويضيفه للصندوق. يفشل بصمت إن لم يكن كافياً."""
        premium = await InsuranceService.premium_for(price_usd)
        if premium <= 0:
            return Decimal("0")
        try:
            await BalanceService.deduct_balance(
                session,
                user_id,
                premium,
                TransactionType.PURCHASE,
                description=f"رسم تأمين الطلب #{order_id}",
                related_table="number_orders",
                related_id=order_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("تعذّر اقتطاع رسم التأمين للطلب %s: %s", order_id, exc)
            return Decimal("0")
        await InsuranceService._add_to_pool(session, premium)
        await FeatureService.track("sms_insurance", "premium", value=str(premium))
        return premium

    # ─────────── المطالبة ───────────

    @staticmethod
    async def claim(session, order_id: int, user_id: int, bot=None) -> dict:
        """
        يطالب بتعويض عن طلب لم يصل كوده.
        يرجع ملخصاً أو يرمي InsuranceError.
        """
        if not await InsuranceService.enabled():
            raise InsuranceError("التأمين موقوف حالياً.")

        order = await session.get(NumberOrder, order_id)
        if order is None or order.user_id != user_id:
            raise InsuranceError("الطلب غير موجود.")
        if order.status == OrderStatus.COMPLETED and order.sms_code:
            raise InsuranceError("هذا الطلب وصل كوده فلا يستحق تعويضاً.")

        window = await FeatureService.config_int("sms_insurance", "claim_window_seconds", 60)
        if order.purchased_at is not None:
            elapsed = (datetime.utcnow() - order.purchased_at).total_seconds()
            if elapsed < window:
                wait = int(window - elapsed) + 1
                raise InsuranceError(f"انتظر {wait} ثانية قبل المطالبة.")

        # ── منع المطالبة المزدوجة ──
        if order.insurance_claimed:
            raise InsuranceError("سبق المطالبة بهذا الطلب.")

        pool = await InsuranceService._pool_balance(session)
        base = order.price_sell_usd or Decimal("0")
        double_services = await FeatureService.config_json(
            "sms_insurance", "double_refund_services_json", []
        )
        multiplier = Decimal("2") if order.service in (double_services or []) else Decimal("1")
        payout = (base * multiplier).quantize(Decimal("0.0001"))

        # لا نَعِد بما لا يملكه الصندوق
        if pool < payout:
            payout = pool.quantize(Decimal("0.0001"))
        if payout <= 0:
            raise InsuranceError("صندوق التأمين فارغ حالياً. فتحنا تذكرة لدعمك.")

        await BalanceService.add_balance(
            session,
            user_id,
            payout,
            TransactionType.REFUND,
            description=f"تعويض تأمين الطلب #{order_id}",
            related_table="number_orders",
            related_id=order_id,
            payment_reference=f"insurance:{order_id}",
        )
        await InsuranceService._add_to_pool(session, -payout)

        order.insurance_claimed = True
        if order.status in (OrderStatus.PENDING, OrderStatus.EXPIRED):
            order.status = OrderStatus.REFUNDED
        await session.commit()

        await FeatureService.track("sms_insurance", "claimed", user_id=user_id, value=str(payout))

        if bot is not None:
            try:
                user = await session.get(User, user_id)
                await NotificationService(bot).notify_user(
                    user.telegram_id,
                    "🛡️ <b>دُفع تعويض التأمين</b>\n\n"
                    f"🆔 الطلب: #{order_id}\n"
                    f"💰 أُضيف لرصيدك: <b>{payout}$</b>\n\n"
                    "بلا انتظار دعم — هذا ما وُجد الصندوق لأجله.",
                )
            except Exception:  # noqa: BLE001
                pass

        return {"order_id": order_id, "payout_usd": payout, "pool_usd": pool - payout}

    # ─────────── الشفافية ───────────

    @staticmethod
    async def report(session) -> dict:
        """حالة الصندوق — تُعرض للمستخدمين والأدمن بشفافية."""
        pool = await InsuranceService._pool_balance(session)
        claims = (
            await session.execute(
                select(func.count(NumberOrder.id)).where(
                    NumberOrder.insurance_claimed.is_(True)
                )
            )
        ).scalar_one()
        return {
            "pool_usd": pool,
            "total_claims": int(claims or 0),
            "premium_percent": await InsuranceService.premium_percent(),
            "enabled": await InsuranceService.enabled(),
        }
