from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from database.engine import async_session_maker
from database.models import EscrowStatus, MarketListingStatus, User
from sqlalchemy import select
from services.feature_service import FeatureService
from services.market_profile_service import MarketProfileService
from services.marketplace_service import MarketplaceService


async def _setup_market_users():
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "peer_marketplace", True)
        await FeatureService.set_enabled(session, "trusted_seller_auto_approve", False)
        seller = User(
            telegram_id=91001,
            username="seller",
            balance=Decimal("20"),
            joined_at=datetime.utcnow() - timedelta(days=10),
        )
        buyer = User(
            telegram_id=91002,
            username="buyer",
            balance=Decimal("50"),
            joined_at=datetime.utcnow() - timedelta(days=10),
        )
        session.add_all([seller, buyer])
        await session.commit()
        await session.refresh(seller)
        await session.refresh(buyer)
        admin = (await session.execute(select(User).where(User.is_admin.is_(True)))).scalars().first()
        await MarketProfileService.create(session, seller.id, "seller_alias", "secret123")
        return seller.id, buyer.id, admin.id if admin else seller.id


@pytest.mark.asyncio
async def test_marketplace_secret_listing_reveals_only_to_buyer_and_release_pays_seller():
    seller_id, buyer_id, admin_id = await _setup_market_users()
    async with async_session_maker() as session:
        listing = await MarketplaceService.create_listing(
            session,
            seller_id=seller_id,
            kind="digital_code",
            title="بطاقة رقمية",
            description="كود مضمون",
            price_usd=Decimal("10"),
            secret_code="CODE-123",
        )
        assert listing.status == MarketListingStatus.PENDING_REVIEW
        await MarketplaceService.approve(session, listing.id, admin_id, Decimal("10"))
        tx = await MarketplaceService.purchase(session, listing.id, buyer_id)

        seller = await session.get(User, seller_id)
        buyer = await session.get(User, buyer_id)
        assert buyer.balance == Decimal("39.0000")
        assert seller.balance == Decimal("20.0000")
        assert await MarketplaceService.reveal_secret(session, tx.id, buyer_id) == "CODE-123"

        assert await MarketplaceService.release(session, tx.id, admin_id=admin_id) is True
        await session.refresh(seller)
        await session.refresh(tx)
        profile = await MarketProfileService.get(session, seller_id)
        assert seller.balance == Decimal("30.0000")
        assert tx.status == EscrowStatus.RELEASED
        assert profile.successful_sales == 1


@pytest.mark.asyncio
async def test_marketplace_bad_secret_dispute_records_failure_and_refund_restores_buyer():
    seller_id, buyer_id, admin_id = await _setup_market_users()
    async with async_session_maker() as session:
        listing = await MarketplaceService.create_listing(
            session,
            seller_id=seller_id,
            kind="game_account",
            title="حساب لعبة",
            description="حساب كامل",
            price_usd=Decimal("10"),
            secret_code="user:bad pass:bad",
        )
        assert listing.secret_payload is not None
        await MarketplaceService.approve(session, listing.id, admin_id, Decimal("10"))
        tx = await MarketplaceService.purchase(session, listing.id, buyer_id)
        assert await MarketplaceService.reveal_secret(session, tx.id, buyer_id) == "user:bad pass:bad"

        assert await MarketplaceService.dispute(session, tx.id, buyer_id, "المعلومات خاطئة") is True
        profile = await MarketProfileService.get(session, seller_id)
        assert profile.failed_sales == 1
        assert profile.disputes_count == 1

        assert await MarketplaceService.refund(session, tx.id, admin_id=admin_id, note="كسب المشتري") is True
        buyer = await session.get(User, buyer_id)
        await session.refresh(tx)
        assert buyer.balance == Decimal("50.0000")
        assert tx.status == EscrowStatus.REFUNDED
