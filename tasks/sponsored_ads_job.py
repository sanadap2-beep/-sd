"""Scheduled repost/expiry for sponsored ads."""

from database.engine import async_session_maker
from services.sponsored_ad_service import SponsoredAdService


async def process_sponsored_ads(bot):
    async with async_session_maker() as session:
        await SponsoredAdService.expire_due(session)
        await SponsoredAdService.repost_due(session, bot)
