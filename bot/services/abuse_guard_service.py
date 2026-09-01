"""Persistent abuse signals for high-risk operations."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select

from database.models import AbuseEvent


class AbuseGuardService:
    BLOCK_SCORE = 20

    @classmethod
    async def record(
        cls,
        session,
        user_id: int | None,
        event_type: str,
        score: int = 1,
        details: str | None = None,
    ) -> None:
        if score <= 0:
            return
        session.add(
            AbuseEvent(
                user_id=user_id,
                event_type=event_type[:64],
                score=score,
                details=details[:500] if details else None,
            )
        )
        await session.commit()

    @classmethod
    async def score(cls, session, user_id: int) -> int:
        since = datetime.utcnow() - timedelta(hours=1)
        value = await session.execute(
            select(func.coalesce(func.sum(AbuseEvent.score), 0)).where(
                AbuseEvent.user_id == user_id,
                AbuseEvent.created_at >= since,
            )
        )
        return int(value.scalar_one())

    @classmethod
    async def is_blocked(cls, session, user_id: int) -> bool:
        return await cls.score(session, user_id) >= cls.BLOCK_SCORE

    @classmethod
    async def record_checkout_failure(cls, session, user_id: int, reason: str) -> None:
        await cls.record(session, user_id, "checkout_failure", 2, reason)

    @classmethod
    async def record_payment_failure(cls, session, user_id: int, reason: str) -> None:
        await cls.record(session, user_id, "payment_failure", 4, reason)
