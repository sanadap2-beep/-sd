"""
خدمة التحديات الأسبوعية.

الأدمن ينشئ تحدياً (المقياس + الهدف + المكافأة) ويُفعّله لتسلسل أسبوعي
يتجدد تلقائياً. تقدم المستخدمين يُحدَّث من الأحداث الحقيقية عبر
increment_progress (idempotent بقيد فريد)، والاستلام يتم بضغطة واحدة.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.models import TransactionType, WeeklyChallenge, WeeklyChallengeProgress
from services.balance_service import BalanceService
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


class ChallengeError(Exception):
    """خطأ واضح في التحدي الأسبوعي."""


def _iso_week() -> str:
    today = date.today()
    return f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"


class WeeklyChallengeService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("weekly_challenges")

    @staticmethod
    async def active_challenge(session, week: str | None = None) -> WeeklyChallenge | None:
        week = week or _iso_week()
        result = await session.execute(
            select(WeeklyChallenge)
            .where(WeeklyChallenge.is_active.is_(True), WeeklyChallenge.week_start == week)
            .order_by(WeeklyChallenge.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def all_challenges(session) -> list[WeeklyChallenge]:
        result = await session.execute(
            select(WeeklyChallenge).order_by(WeeklyChallenge.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def create_challenge(
        session,
        title: str,
        description: str,
        emoji: str,
        metric: str,
        target_value: Decimal,
        reward_usd: Decimal,
        reward_points: int,
        week_start: str | None = None,
    ) -> WeeklyChallenge:
        if metric not in ("orders", "spend_usd", "checkins", "referrals"):
            raise ChallengeError("مقياس غير مدعوم.")
        challenge = WeeklyChallenge(
            title=title[:128],
            description=description[:500],
            emoji=emoji or "🏆",
            metric=metric,
            target_value=target_value,
            reward_usd=reward_usd,
            reward_points=max(0, int(reward_points)),
            week_start=week_start or _iso_week(),
            is_active=True,
        )
        session.add(challenge)
        await session.commit()
        await session.refresh(challenge)
        return challenge

    @staticmethod
    async def toggle_challenge(session, challenge_id: int) -> WeeklyChallenge | None:
        challenge = await session.get(WeeklyChallenge, challenge_id)
        if challenge is None:
            return None
        challenge.is_active = not challenge.is_active
        await session.commit()
        await session.refresh(challenge)
        return challenge

    @staticmethod
    async def delete_challenge(session, challenge_id: int) -> bool:
        challenge = await session.get(WeeklyChallenge, challenge_id)
        if challenge is None:
            return False
        await session.delete(challenge)
        await session.commit()
        return True

    @staticmethod
    async def get_progress(session, challenge_id: int, user_id: int) -> WeeklyChallengeProgress | None:
        result = await session.execute(
            select(WeeklyChallengeProgress).where(
                WeeklyChallengeProgress.challenge_id == challenge_id,
                WeeklyChallengeProgress.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def increment_progress(
        session,
        challenge_id: int,
        user_id: int,
        amount: Decimal = Decimal("1"),
    ) -> bool:
        """أضف تقدماً للمستخدم في التحدي (الاستدعاء مزدوج آمن)."""
        progress = await WeeklyChallengeService.get_progress(session, challenge_id, user_id)
        if progress is None:
            progress = WeeklyChallengeProgress(
                challenge_id=challenge_id, user_id=user_id, progress=amount, claimed=False
            )
            session.add(progress)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                progress = await WeeklyChallengeService.get_progress(session, challenge_id, user_id)
                if progress is None:
                    return False
                progress.progress += amount
                await session.commit()
            return True
        progress.progress += amount
        await session.commit()
        return True

    @staticmethod
    async def record_event(
        session,
        user_id: int,
        amount: Decimal = Decimal("1"),
        event: str = "orders",
    ) -> bool:
        """
        سجّل حدثاً حقيقياً في التحدي النشط لهذا الأسبوع.
        event: orders / spend_usd / checkins / referrals — يُطابق مقياس
        التحدي، ويُضاف المبلغ = 1 للأحداث العدّية أو amount للنقدي.
        """
        challenge = await WeeklyChallengeService.active_challenge(session)
        if challenge is None or not await WeeklyChallengeService.enabled():
            return False
        metric = (challenge.metric or "orders").lower()
        if event == "spend_usd" and metric == "spend_usd":
            by = amount
        elif metric in ("orders", "checkins", "referrals") and event == metric:
            by = Decimal("1")
        else:
            return False
        return await WeeklyChallengeService.increment_progress(
            session, challenge.id, user_id, by
        )

    @staticmethod
    async def claim_reward(session, user_id: int) -> dict | None:
        """
        استلام مكافأة التحدي النشط لهذا الأسبوع إذا اكتمل الهدف.
        تُرجع تفاصيل المكافأة أو None إذا لا يوجد شيء للاستلام.
        """
        challenge = await WeeklyChallengeService.active_challenge(session)
        if challenge is None:
            return None
        progress = await WeeklyChallengeService.get_progress(session, challenge.id, user_id)
        if progress is None or progress.claimed:
            return None
        if progress.progress < challenge.target_value:
            return None

        async with BalanceService._get_lock(user_id):
            progress.claimed = True
            from datetime import datetime

            progress.claimed_at = datetime.utcnow()
            claimed_reward = Decimal("0")
            if challenge.reward_usd > 0:
                await BalanceService.add_balance(
                    session,
                    user_id,
                    challenge.reward_usd,
                    TransactionType.COUPON_BONUS,
                    description=f"جائزة التحدي الأسبوعي: {challenge.title}",
                    related_table="weekly_challenges",
                    related_id=challenge.id,
                    payment_reference=f"challenge:{user_id}:{challenge.id}",
                )
                claimed_reward = challenge.reward_usd
            if challenge.reward_points > 0:
                from services.loyalty_service import LoyaltyService

                await LoyaltyService.award_points(
                    session,
                    user_id,
                    challenge.reward_points,
                    event_key=f"challenge:{user_id}:{challenge.id}",
                    event_type="weekly_challenge",
                    description=f"نقاط التحدي: {challenge.title}",
                    related_table="weekly_challenges",
                    related_id=challenge.id,
                )
                await session.commit()
            return {
                "challenge": challenge,
                "reward_usd": claimed_reward,
                "reward_points": challenge.reward_points,
            }
        return None