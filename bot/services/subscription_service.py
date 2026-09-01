from sqlalchemy import select

from database.models import MandatoryChannel


class SubscriptionService:
    @staticmethod
    async def get_active_channels(session):
        result = await session.execute(
            select(MandatoryChannel).where(MandatoryChannel.is_active.is_(True))
        )
        return result.scalars().all()

    @staticmethod
    async def is_user_subscribed_all(bot, session, user_telegram_id: int) -> tuple[bool, list]:
        channels = await SubscriptionService.get_active_channels(session)
        not_subscribed = []
        for ch in channels:
            try:
                member = await bot.get_chat_member(ch.chat_id, user_telegram_id)
                if member.status in ("left", "kicked"):
                    not_subscribed.append(ch)
            except Exception:
                not_subscribed.append(ch)
        return len(not_subscribed) == 0, not_subscribed
