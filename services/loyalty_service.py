"""برنامج الولاء: نقاط، مستويات، تسجيل يومي واستبدال آمن."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_DOWN
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.models import (
    LoyaltyEvent,
    Transaction,
    TransactionType,
    User,
)
from services.balance_service import BalanceService
from services.settings_service import SettingsService


@dataclass(frozen=True)
class LoyaltyTier:
    name: str
    emoji: str
    minimum_points: int
    points_multiplier: Decimal
    benefit: str


TIERS = (
    LoyaltyTier("Bronze", "🥉", 0, Decimal("1"), "نقاط أساسية على كل شراء"),
    LoyaltyTier("Silver", "🥈", 500, Decimal("1.25"), "نقاط أكثر بنسبة 25%"),
    LoyaltyTier("Gold", "🥇", 2_000, Decimal("1.5"), "نقاط أكثر بنسبة 50%"),
    LoyaltyTier("Platinum", "💎", 5_000, Decimal("1.75"), "نقاط أكثر بنسبة 75%"),
    LoyaltyTier("VIP", "👑", 10_000, Decimal("2"), "نقاط مضاعفة على كل شراء"),
)


class LoyaltyError(Exception):
    """خطأ واضح في برنامج الولاء."""


class LoyaltyService:
    @staticmethod
    def tier_for_points(points: int) -> LoyaltyTier:
        current = TIERS[0]
        for tier in TIERS:
            if points >= tier.minimum_points:
                current = tier
            else:
                break
        return current

    @staticmethod
    def next_tier(points: int) -> LoyaltyTier | None:
        for tier in TIERS:
            if points < tier.minimum_points:
                return tier
        return None

    @staticmethod
    async def get_summary(session, user_id: int) -> dict:
        user = await session.get(User, user_id)
        if user is None:
            raise LoyaltyError("المستخدم غير موجود")
        points = user.loyalty_points or 0
        tier = LoyaltyService.tier_for_points(points)
        next_tier = LoyaltyService.next_tier(points)
        if next_tier:
            progress = points - tier.minimum_points
            required = next_tier.minimum_points - tier.minimum_points
        else:
            progress = required = 0
        return {
            "points": points,
            "streak": user.loyalty_streak or 0,
            "last_checkin_date": user.last_checkin_date,
            "tier": tier,
            "next_tier": next_tier,
            "progress": progress,
            "required": required,
        }

    @staticmethod
    async def award_points(
        session,
        user_id: int,
        points: int,
        event_key: str,
        event_type: str,
        description: str,
        related_table: str | None = None,
        related_id: int | None = None,
    ) -> bool:
        """أضف نقاطاً مرة واحدة فقط باستخدام event_key idempotent."""
        if points == 0:
            return False
        async with BalanceService._get_lock(user_id):
            user = await session.get(User, user_id)
            if user is None:
                raise LoyaltyError("المستخدم غير موجود")
            existing = await session.execute(
                select(LoyaltyEvent).where(LoyaltyEvent.event_key == event_key)
            )
            if existing.scalar_one_or_none() is not None:
                return False

            user.loyalty_points = max(0, (user.loyalty_points or 0) + points)
            session.add(
                LoyaltyEvent(
                    user_id=user_id,
                    event_key=event_key,
                    event_type=event_type,
                    points=points,
                    related_table=related_table,
                    related_id=related_id,
                    description=description[:255],
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return False
            return True

    @staticmethod
    async def award_purchase_points(
        session,
        user_id: int,
        order_type: str,
        order_id: int,
        amount_usd: Decimal,
    ) -> int:
        rate = await SettingsService.get_decimal("loyalty_points_per_usd", Decimal("10"))
        user = await session.get(User, user_id)
        current_points = user.loyalty_points if user else 0
        multiplier = LoyaltyService.tier_for_points(current_points or 0).points_multiplier
        points = int((amount_usd * rate * multiplier).to_integral_value(rounding=ROUND_DOWN))
        if points <= 0:
            return 0
        awarded = await LoyaltyService.award_points(
            session,
            user_id,
            points,
            event_key=f"purchase:{order_type}:{order_id}",
            event_type="purchase",
            description=f"نقاط شراء {order_type} #{order_id}",
            related_table=order_type,
            related_id=order_id,
        )
        return points if awarded else 0

    @staticmethod
    async def claim_daily(
        session,
        user_id: int,
    ) -> tuple[int, int, bool]:
        """استلام مكافأة اليوم: (النقاط، السلسلة، تم الاستلام مسبقاً)."""
        today = date.today()
        base_points = await SettingsService.get_int("loyalty_daily_points", 25)
        async with BalanceService._get_lock(user_id):
            user = await session.get(User, user_id)
            if user is None:
                raise LoyaltyError("المستخدم غير موجود")
            event_key = f"daily_checkin:{user_id}:{today.isoformat()}"
            existing = await session.execute(
                select(LoyaltyEvent).where(LoyaltyEvent.event_key == event_key)
            )
            if existing.scalar_one_or_none() is not None:
                return 0, user.loyalty_streak or 0, True

            yesterday = today - timedelta(days=1)
            if user.last_checkin_date == yesterday:
                streak = (user.loyalty_streak or 0) + 1
            else:
                streak = 1
            # Keep the bonus predictable while rewarding a weekly streak.
            points = base_points + min(streak - 1, 6) * 5
            user.loyalty_streak = streak
            user.last_checkin_date = today
            user.loyalty_points = (user.loyalty_points or 0) + points
            session.add(
                LoyaltyEvent(
                    user_id=user_id,
                    event_key=event_key,
                    event_type="daily_checkin",
                    points=points,
                    description=f"مكافأة تسجيل يومي - يوم {streak}",
                )
            )
            await session.commit()
            return points, streak, False

    @staticmethod
    async def redeem_points(
        session,
        user_id: int,
        points: int,
    ) -> Decimal:
        ratio = await SettingsService.get_int("loyalty_points_per_usd_redeem", 1000)
        minimum = await SettingsService.get_int("loyalty_min_redeem_points", 100)
        if ratio <= 0 or points < minimum:
            raise LoyaltyError(f"الحد الأدنى للاستبدال هو {minimum} نقطة.")
        amount = (Decimal(points) / Decimal(ratio)).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        if amount <= 0:
            raise LoyaltyError("عدد النقاط غير كافٍ للاستبدال.")

        async with BalanceService._get_lock(user_id):
            user = await session.get(User, user_id)
            if user is None:
                raise LoyaltyError("المستخدم غير موجود")
            if (user.loyalty_points or 0) < points:
                raise LoyaltyError("رصيد نقاطك غير كافٍ.")

            event = LoyaltyEvent(
                user_id=user_id,
                event_key=f"redeem:{user_id}:{uuid4().hex}",
                event_type="redeem",
                points=-points,
                description=f"استبدال {points} نقطة مقابل {amount}$",
            )
            user.loyalty_points -= points
            user.balance += amount
            session.add(event)
            await session.flush()
            session.add(
                Transaction(
                    user_id=user_id,
                    type=TransactionType.LOYALTY_REDEEM,
                    amount=amount,
                    balance_after=user.balance,
                    related_table="loyalty_events",
                    related_id=event.id,
                    payment_reference=f"loyalty_redeem:{event.id}",
                    description=f"استبدال {points} نقطة ولاء",
                )
            )
            await session.commit()
            return amount
