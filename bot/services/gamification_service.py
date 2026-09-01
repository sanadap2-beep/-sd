"""تحديات تفاعلية مرتبطة بالأحداث، مع مكافآت ولاء idempotent."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from database.models import Challenge, ChallengeProgress, ChallengeStatus
from services.loyalty_service import LoyaltyService


class GamificationService:
    @staticmethod
    async def get_active(session) -> list[Challenge]:
        now = datetime.utcnow()
        result = await session.execute(
            select(Challenge)
            .where(
                Challenge.status == ChallengeStatus.ACTIVE,
                Challenge.starts_at <= now,
                (Challenge.ends_at.is_(None) | (Challenge.ends_at > now)),
            )
            .order_by(Challenge.reward_points.desc(), Challenge.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def user_progress(
        session, user_id: int
    ) -> list[tuple[Challenge, ChallengeProgress | None]]:
        challenges = await GamificationService.get_active(session)
        result = (
            await session.execute(
                select(ChallengeProgress).where(
                    ChallengeProgress.user_id == user_id,
                    ChallengeProgress.challenge_id.in_([challenge.id for challenge in challenges]),
                )
            )
            if challenges
            else None
        )
        progress = {row.challenge_id: row for row in result.scalars().all()} if result else {}
        return [(challenge, progress.get(challenge.id)) for challenge in challenges]

    @staticmethod
    async def progress_event(
        session,
        user_id: int,
        event_type: str,
        increment: int = 1,
    ) -> int:
        """تقدم المستخدم في التحديات، ويرجع مجموع المكافآت الجديدة."""
        if increment <= 0:
            return 0
        earned = 0
        for challenge in await GamificationService.get_active(session):
            if challenge.event_type != event_type:
                continue
            result = await session.execute(
                select(ChallengeProgress).where(
                    ChallengeProgress.challenge_id == challenge.id,
                    ChallengeProgress.user_id == user_id,
                )
            )
            progress = result.scalar_one_or_none()
            if progress is None:
                progress = ChallengeProgress(
                    challenge_id=challenge.id,
                    user_id=user_id,
                    progress=0,
                    completed=False,
                )
                session.add(progress)
                await session.flush()
            if progress.completed:
                continue
            progress.progress = min(
                challenge.target_value,
                progress.progress + increment,
            )
            if progress.progress >= challenge.target_value:
                progress.completed = True
                progress.completed_at = datetime.utcnow()
                awarded = await LoyaltyService.award_points(
                    session,
                    user_id,
                    challenge.reward_points,
                    event_key=f"challenge:{challenge.id}:{user_id}",
                    event_type="challenge",
                    description=f"مكافأة التحدي: {challenge.title}",
                    related_table="challenges",
                    related_id=challenge.id,
                )
                if awarded:
                    earned += challenge.reward_points
        await session.commit()
        return earned
