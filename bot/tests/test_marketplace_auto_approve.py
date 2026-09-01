from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from database.engine import async_session_maker
from database.models import MarketListingStatus, MarketProfile, User
from services.feature_service import FeatureService
from services.marketplace_service import MarketplaceService


@pytest.mark.asyncio
async def test_trusted_seller_listing_auto_approved():
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "peer_marketplace", True)
        await FeatureService.set_enabled(session, "trusted_seller_auto_approve", True)
        await FeatureService.set_option(session, "trusted_seller_auto_approve", "min_successful_sales", "5")
        await FeatureService.set_option(session, "trusted_seller_auto_approve", "min_success_rate", "90")
        seller = User(
            telegram_id=88001,
            balance=Decimal("20"),
            joined_at=datetime.utcnow() - timedelta(days=5),
        )
        session.add(seller)
        await session.commit()
        await session.refresh(seller)
        session.add(
            MarketProfile(
                user_id=seller.id,
                alias="trusted_seller",
                password_hash="x",
                successful_sales=10,
                failed_sales=0,
            )
        )
        await session.commit()

        listing = await MarketplaceService.create_listing(
            session,
            seller_id=seller.id,
            kind="digital_code",
            title="كود مضمون",
            description="كود اختبار",
            price_usd=Decimal("10"),
            secret_code="SECRET-CODE",
        )

        assert listing.status == MarketListingStatus.APPROVED
        assert listing.published_at is not None


@pytest.mark.asyncio
async def test_new_seller_listing_stays_pending_review():
    async with async_session_maker() as session:
        await FeatureService.set_enabled(session, "peer_marketplace", True)
        await FeatureService.set_enabled(session, "trusted_seller_auto_approve", True)
        seller = User(
            telegram_id=88002,
            balance=Decimal("20"),
            joined_at=datetime.utcnow() - timedelta(days=5),
        )
        session.add(seller)
        await session.commit()
        await session.refresh(seller)

        listing = await MarketplaceService.create_listing(
            session,
            seller_id=seller.id,
            kind="digital_code",
            title="كود جديد",
            description="كود اختبار",
            price_usd=Decimal("10"),
            secret_code="SECRET-CODE",
        )

        assert listing.status == MarketListingStatus.PENDING_REVIEW
