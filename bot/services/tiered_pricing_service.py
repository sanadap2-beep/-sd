"""Discounts based on loyalty tier and order volume."""

from __future__ import annotations

from decimal import Decimal

from database.models import Product, User
from services.loyalty_service import LoyaltyService


class TieredPricingService:
    TIER_DISCOUNTS = {
        "Bronze": Decimal("0"),
        "Silver": Decimal("1"),
        "Gold": Decimal("2"),
        "Platinum": Decimal("3"),
        "VIP": Decimal("5"),
    }

    @classmethod
    async def discount_for(
        cls,
        session,
        user_id: int,
        product: Product,
        amount: Decimal,
        quantity: int = 1,
    ) -> tuple[Decimal, str]:
        user = await session.get(User, user_id)
        points = user.loyalty_points if user else 0
        tier = LoyaltyService.tier_for_points(points or 0)
        tier_percent = cls.TIER_DISCOUNTS.get(tier.name, Decimal("0"))
        volume_percent = Decimal("2") if quantity >= 1000 else Decimal("0")
        percent = max(tier_percent, volume_percent)
        discount = (amount * percent / Decimal("100")).quantize(Decimal("0.0001"))
        label = f"{tier.name} -{percent}%" if tier_percent >= volume_percent else "حسم الكمية -2%"
        return min(amount, discount), label
