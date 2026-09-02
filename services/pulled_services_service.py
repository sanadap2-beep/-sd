"""Admin catalog of pulled SMM provider services.

Pulled services stay hidden from the storefront until the admin publishes
one into a subcategory they created, with an explicit selling price.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import (
    ProductDisplayType,
    ProductFulfillmentType,
    ProviderService,
    ProviderServiceStatus,
    SubCategory,
)
from services.dynamic_service import DynamicService
from services.smm_catalog import (
    OTHER_KIND_KEY,
    OTHER_PLATFORM_KEY,
    classify_smm_service,
    kind_meta,
    platform_meta,
)

SERVICES_PER_PAGE = 8


class PulledServicesService:
    @staticmethod
    def classify(service: ProviderService) -> tuple[str, str]:
        return classify_smm_service(service.name, service.category, service.service_type)

    @staticmethod
    async def load_active(session) -> list[ProviderService]:
        result = await session.execute(
            select(ProviderService)
            .options(selectinload(ProviderService.api_provider))
            .where(ProviderService.status == ProviderServiceStatus.ACTIVE)
        )
        return list(result.scalars().all())

    @staticmethod
    async def platform_counts(session) -> list[tuple[str, str, str, int]]:
        """``[(platform_key, emoji, label, count), ...]`` cheapest-platform first by count desc."""
        services = await PulledServicesService.load_active(session)
        counts: dict[str, int] = defaultdict(int)
        for service in services:
            platform, _kind = PulledServicesService.classify(service)
            counts[platform] += 1
        rows = []
        for key, count in counts.items():
            emoji, label = platform_meta(key)
            rows.append((key, emoji, label, count))
        rows.sort(key=lambda row: (row[0] == OTHER_PLATFORM_KEY, -row[3], row[2]))
        return rows

    @staticmethod
    async def kind_counts(session, platform_key: str) -> list[tuple[str, str, str, int]]:
        services = await PulledServicesService.load_active(session)
        counts: dict[str, int] = defaultdict(int)
        for service in services:
            platform, kind = PulledServicesService.classify(service)
            if platform != platform_key:
                continue
            counts[kind] += 1
        rows = []
        for key, count in counts.items():
            emoji, label = kind_meta(key)
            rows.append((key, emoji, label, count))
        rows.sort(key=lambda row: (row[0] == OTHER_KIND_KEY, -row[3], row[2]))
        return rows

    @staticmethod
    async def list_services(
        session,
        platform_key: str,
        kind_key: str,
        page: int = 0,
        per_page: int = SERVICES_PER_PAGE,
    ) -> tuple[list[ProviderService], int]:
        services = await PulledServicesService.load_active(session)
        matched = []
        for service in services:
            platform, kind = PulledServicesService.classify(service)
            if platform == platform_key and kind == kind_key:
                matched.append(service)
        matched.sort(key=lambda s: (Decimal(str(s.rate_usd or 0)), s.id))
        total = len(matched)
        page = max(0, page)
        start = page * per_page
        return matched[start : start + per_page], total

    @staticmethod
    async def destination_subcategories(session) -> list[SubCategory]:
        result = await session.execute(
            select(SubCategory)
            .options(selectinload(SubCategory.category))
            .where(SubCategory.is_active.is_(True))
            .order_by(SubCategory.sort_order, SubCategory.id)
        )
        return list(result.scalars().all())

    @staticmethod
    def parse_sell_price(raw: str) -> Decimal:
        try:
            price = Decimal((raw or "").strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("أرسل رقماً صحيحاً أكبر من صفر.") from exc
        if not price.is_finite() or price <= 0:
            raise ValueError("أرسل رقماً صحيحاً أكبر من صفر.")
        return price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    @staticmethod
    async def publish(
        session,
        service: ProviderService,
        sub_category_id: int,
        sell_price: Decimal,
        name_ar: str | None = None,
    ):
        """Create a storefront product from a pulled service. Hidden until this call."""
        product = await DynamicService.create_product(
            session=session,
            sub_category_id=sub_category_id,
            name_ar=(name_ar or service.name or "خدمة رشق")[:128],
            description=(service.description or service.category or "")[:500] or None,
            price_usd=sell_price,
            cost_price_usd=Decimal(str(service.rate_usd or 0)),
            api_provider_id=service.api_provider_id,
            provider_service_id=service.external_service_id,
            provider_service_ref_id=service.id,
            fulfillment_type=ProductFulfillmentType.API,
            min_quantity=int(service.min_quantity or 1),
            max_quantity=int(service.max_quantity or 1),
            requires_link=bool(service.requires_link),
            requires_player_id=bool(service.requires_player_id),
            requires_quantity=bool(service.requires_quantity),
            display_type=(
                ProductDisplayType.PER_1000
                if service.requires_quantity
                else ProductDisplayType.FIXED_TOTAL
            ),
        )
        return product
