"""
النقاط كعملة شراء.

المطلوب: النقاط التي يجمعها المستخدم يشتري بها الخدمات مباشرة،
وكل عدد محدد من النقاط (افتراضياً 100) يساوي دولاراً واحداً.

الفرق عن LoyaltyService.redeem_points:
- redeem يحوّل النقاط إلى رصيد (خطوة وسيطة).
- هذه الخدمة تدفع **مباشرة** من النقاط عند الشراء، ويمكن مزجها
  مع الرصيد في عملية دفع واحدة.

كل العمليات داخل قفل المستخدم نفسه المستخدم في BalanceService
حتى لا يحدث سباق بين دفعين متزامنين.
"""

from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.models import LoyaltyEvent, Transaction, TransactionType, User
from services.balance_service import BalanceService
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class PointsError(Exception):
    pass


class PointsService:
    # ─────────── التسعير ───────────

    @staticmethod
    async def points_per_usd() -> int:
        """كم نقطة تساوي دولاراً واحداً (افتراضياً 100)."""
        value = await FeatureService.config_int("points_currency", "points_per_usd", 100)
        return value if value > 0 else 100

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("points_currency")

    @staticmethod
    async def usd_for_points(points: int) -> Decimal:
        rate = await PointsService.points_per_usd()
        return (Decimal(points) / Decimal(rate)).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )

    @staticmethod
    async def points_for_usd(amount_usd: Decimal) -> int:
        """عدد النقاط المطلوب لتغطية مبلغ بالدولار (يُقرَّب لأعلى)."""
        rate = await PointsService.points_per_usd()
        if amount_usd <= 0:
            return 0
        raw = amount_usd * Decimal(rate)
        return int(-(-raw // 1))  # ceil بدون تحويل float

    @staticmethod
    async def max_payment_percent() -> int:
        """أقصى نسبة من قيمة الطلب يمكن دفعها بالنقاط (0–100)."""
        value = await FeatureService.config_int(
            "points_currency", "max_points_payment_percent", 100
        )
        return max(0, min(100, value))

    # ─────────── الحساب ───────────

    @staticmethod
    async def affordable_usd(points: int, order_total_usd: Decimal) -> Decimal:
        """
        كم دولاراً تغطيه نقاط المستخدم من هذا الطلب،
        مع احترام الحد الأقصى لنسبة الدفع بالنقاط.
        """
        if points <= 0 or order_total_usd <= 0:
            return Decimal("0")
        cap_percent = await PointsService.max_payment_percent()
        cap_usd = (order_total_usd * Decimal(cap_percent) / Decimal(100)).quantize(
            Decimal("0.0001"), rounding=ROUND_DOWN
        )
        from_points = await PointsService.usd_for_points(points)
        return min(from_points, cap_usd, order_total_usd)

    @staticmethod
    async def quote(session, user_id: int, order_total_usd: Decimal) -> dict:
        """
        يعرض خطة الدفع: كم من النقاط وكم من الرصيد.
        لا يخصم شيئاً — للتأكيد قبل التنفيذ فقط.
        """
        user = await session.get(User, user_id)
        points = (user.loyalty_points or 0) if user else 0
        if not await PointsService.enabled():
            points = 0
        points_usd = await PointsService.affordable_usd(points, order_total_usd)
        points_used = await PointsService.points_for_usd(points_usd) if points_usd > 0 else 0
        # لا نخصم نقاطاً أكثر مما يغطي القيمة الفعلية المقربة
        if points_usd > 0:
            covered = await PointsService.usd_for_points(points_used)
            if covered > points_usd:
                points_used = int(points_usd * Decimal(await PointsService.points_per_usd()))
        cash_usd = (order_total_usd - points_usd).quantize(Decimal("0.0001"))
        return {
            "total_usd": order_total_usd,
            "points_available": points,
            "points_used": points_used,
            "points_usd": points_usd,
            "cash_usd": cash_usd,
            "rate": await PointsService.points_per_usd(),
        }

    # ─────────── الدفع ───────────

    @staticmethod
    async def spend(
        session,
        user_id: int,
        points: int,
        description: str,
        related_table: str | None = None,
        related_id: int | None = None,
    ) -> Decimal:
        """
        يخصم نقاطاً ويعيد قيمتها بالدولار.
        يرمي PointsError إذا كانت النقاط غير كافية.
        """
        if points <= 0:
            return Decimal("0")
        if not await PointsService.enabled():
            raise PointsError("الدفع بالنقاط موقوف حالياً.")

        async with BalanceService._get_lock(user_id):
            user = await session.get(User, user_id)
            if user is None:
                raise PointsError("المستخدم غير موجود.")
            if (user.loyalty_points or 0) < points:
                raise PointsError("رصيد نقاطك غير كافٍ.")

            value_usd = await PointsService.usd_for_points(points)
            if value_usd <= 0:
                raise PointsError("عدد النقاط غير كافٍ لتغطية أي مبلغ.")

            user.loyalty_points -= points
            session.add(
                LoyaltyEvent(
                    user_id=user_id,
                    event_key=f"spend:{user_id}:{uuid4().hex}",
                    event_type="purchase_spend",
                    points=-points,
                    related_table=related_table,
                    related_id=related_id,
                    description=description[:255],
                )
            )
            session.add(
                Transaction(
                    user_id=user_id,
                    type=TransactionType.LOYALTY_REDEEM,
                    amount=value_usd,
                    balance_after=user.balance,
                    related_table=related_table,
                    related_id=related_id,
                    payment_reference=f"points_spend:{user_id}:{uuid4().hex}",
                    description=f"دفع {points} نقطة ({value_usd}$) — {description}"[:255],
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise PointsError("تعذّر إتمام الدفع بالنقاط، أعد المحاولة.")
            return value_usd

    @staticmethod
    async def split_payment(
        session,
        user_id: int,
        total_usd: Decimal,
        use_points: bool,
        description: str,
        related_table: str | None = None,
        related_id: int | None = None,
    ) -> dict:
        """
        يدفع الطلب: النقاط أولاً (إن طُلبت وكانت كافية)، والباقي من الرصيد.

        يرجع {"points_used", "points_usd", "cash_usd", "paid"}.
        لا يخصم الرصيد — ذلك مسؤولية BalanceService.deduct_balance عند المستدعي،
        حتى تبقى نقطة الخصم واحدة وقابلة للتدقيق.
        """
        if total_usd <= 0:
            return {"points_used": 0, "points_usd": Decimal("0"), "cash_usd": Decimal("0")}

        user = await session.get(User, user_id)
        available = (user.loyalty_points or 0) if user else 0

        points_used = 0
        points_usd = Decimal("0")
        if use_points and available > 0 and await PointsService.enabled():
            points_usd = await PointsService.affordable_usd(available, total_usd)
            if points_usd > 0:
                rate = await PointsService.points_per_usd()
                points_used = int(points_usd * Decimal(rate))
                # نتأكد أننا لا نغطي أكثر من المطلوب بعد التقريب
                while points_used > 0 and await PointsService.usd_for_points(points_used) > points_usd:
                    points_used -= 1
                points_usd = await PointsService.usd_for_points(points_used)

        if points_used > 0:
            await PointsService.spend(
                session,
                user_id,
                points_used,
                description=description,
                related_table=related_table,
                related_id=related_id,
            )

        cash_usd = (total_usd - points_usd).quantize(Decimal("0.0001"))
        return {"points_used": points_used, "points_usd": points_usd, "cash_usd": cash_usd}

    # ─────────── العرض ───────────

    @staticmethod
    async def format(points: int) -> str:
        rate = await PointsService.points_per_usd()
        usd = await PointsService.usd_for_points(points)
        return f"⭐ {points} نقطة (≈ {usd}$)"

    @staticmethod
    async def summary(session, user_id: int) -> str:
        user = await session.get(User, user_id)
        points = (user.loyalty_points or 0) if user else 0
        rate = await PointsService.points_per_usd()
        usd = await PointsService.usd_for_points(points)
        cap = await PointsService.max_payment_percent()
        return (
            f"⭐ <b>نقاطك:</b> {points}\n"
            f"💵 <b>قيمتها:</b> {usd}$\n"
            f"🔁 <b>سعر الصرف:</b> كل {rate} نقطة = 1$\n"
            f"📊 <b>أقصى دفع بالنقاط:</b> {cap}% من قيمة الطلب"
        )
