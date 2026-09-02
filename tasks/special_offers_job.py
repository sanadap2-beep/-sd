"""Scheduled maintenance for 24-hour special offers."""

from database.engine import async_session_maker
from services.special_offer_service import SpecialOfferService


async def process_special_offers(bot):
    async with async_session_maker() as session:
        await SpecialOfferService.maintenance(session, bot)
