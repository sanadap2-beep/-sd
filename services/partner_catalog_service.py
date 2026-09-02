"""Pick a single friend-panel service and publish it into one store section."""

from __future__ import annotations

import json
from decimal import Decimal

from sqlalchemy import select

from database.models import (
    ProductDisplayType,
    ProductFulfillmentType,
    ProviderPriceType,
    ProviderService,
    ProviderServiceStatus,
)
from protocols.base import ProtocolService
from protocols.factory import ProtocolFactory
from protocols.partner_v1 import PARTNER_TYPES, PartnerV1Protocol
from services.pulled_services_service import PulledServicesService

SERVICES_PER_PAGE = 8
SUBS_PER_PAGE = 8


class PartnerCatalogService:
    @staticmethod
    def protocol_for(provider) -> PartnerV1Protocol:
        protocol = ProtocolFactory.create_from_provider(provider)
        if not isinstance(protocol, PartnerV1Protocol):
            raise ValueError("هذا المزود ليس بوت الصديق (Partner V1).")
        return protocol

    @staticmethod
    def type_rows() -> list[tuple[str, str, str]]:
        return list(PARTNER_TYPES)

    @staticmethod
    async def list_live(
        provider,
        type_key: str,
        page: int = 0,
        per_page: int = SERVICES_PER_PAGE,
    ) -> tuple[list[ProtocolService], int]:
        protocol = PartnerCatalogService.protocol_for(provider)
        services = await protocol.get_services(service_type=type_key or None)
        services.sort(key=lambda s: (Decimal(str(s.rate or 0)), s.external_id))
        page = max(0, page)
        start = page * per_page
        return services[start : start + per_page], len(services)

    @staticmethod
    async def fetch_one(provider, service_id: str) -> ProtocolService | None:
        protocol = PartnerCatalogService.protocol_for(provider)
        found = await protocol.get_service(service_id)
        if found is not None:
            return found
        for key, _emoji, _label in PARTNER_TYPES:
            batch = await protocol.get_services(service_type=key)
            for item in batch:
                if item.external_id == str(service_id):
                    return item
        return None

    @staticmethod
    async def upsert_service(session, provider, proto: ProtocolService) -> ProviderService:
        result = await session.execute(
            select(ProviderService).where(
                ProviderService.api_provider_id == provider.id,
                ProviderService.external_service_id == proto.external_id,
            )
        )
        row = result.scalar_one_or_none()
        rate = Decimal(str(proto.rate or 0))
        payload = {
            "name": (proto.name or "خدمة")[:500],
            "category": (proto.category or proto.service_type or "")[:255] or None,
            "service_type": (proto.service_type or "")[:64] or None,
            "rate": rate,
            "rate_usd": rate,
            "price_type": ProviderPriceType.PER_1000 if proto.requires_quantity else ProviderPriceType.FIXED,
            "min_quantity": int(proto.min_quantity or 1),
            "max_quantity": int(proto.max_quantity or 1),
            "description": (proto.description or None),
            "requires_link": bool(proto.requires_link),
            "requires_quantity": bool(proto.requires_quantity),
            "requires_player_id": bool(proto.requires_player_id),
            "supports_refill": bool(proto.supports_refill),
            "supports_cancel": bool(proto.supports_cancel),
            "status": ProviderServiceStatus.ACTIVE,
            "raw_data": json.dumps(proto.raw, ensure_ascii=False)[:4000] if proto.raw else None,
        }
        if row is None:
            row = ProviderService(
                api_provider_id=provider.id,
                external_service_id=str(proto.external_id),
                **payload,
            )
            session.add(row)
        else:
            for key, value in payload.items():
                setattr(row, key, value)
        await session.flush()
        return row

    @staticmethod
    async def publish_one(
        session,
        provider,
        proto: ProtocolService,
        sub_category_id: int,
        sell_price: Decimal,
        name_ar: str | None = None,
    ):
        stored = await PartnerCatalogService.upsert_service(session, provider, proto)
        product = await PulledServicesService.publish(
            session,
            stored,
            sub_category_id,
            sell_price,
            name_ar=name_ar or proto.name,
        )
        # Phone/AI are a fixed price, not per 1000.
        if not proto.requires_quantity and product.display_type != ProductDisplayType.FIXED_TOTAL:
            product.display_type = ProductDisplayType.FIXED_TOTAL
            product.fulfillment_type = ProductFulfillmentType.API
            await session.flush()
        return product
