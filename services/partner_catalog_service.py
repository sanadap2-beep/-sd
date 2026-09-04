"""Pull a friend-panel section into one store subcategory with a profit margin."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import select

from database.models import (
    Product,
    ProductDisplayType,
    ProductFulfillmentType,
    ProductPricingType,
    ProductStatus,
    ProviderPriceType,
    ProviderService,
    ProviderServiceStatus,
)
from protocols.base import ProtocolService
from protocols.factory import ProtocolFactory
from protocols.partner_v1 import PARTNER_TYPES, PartnerV1Protocol, partner_type_meta
from services.pulled_services_service import PulledServicesService
from services.service_localization_service import display_service_name

SERVICES_PER_PAGE = 8
SUBS_PER_PAGE = 8
GROUPS_PER_PAGE = 8
READY_MARKERS = ("جاهز", "ready", "instant")
READY_GROUP_KEY = "ready"
ALL_GROUP_KEY = "all"


def _blob(proto: ProtocolService) -> str:
    return " ".join(
        str(part or "")
        for part in (proto.name, proto.category, proto.description, proto.service_type)
    ).lower()


def is_ready_number(proto: ProtocolService) -> bool:
    blob = _blob(proto)
    return any(marker in blob for marker in READY_MARKERS)


def group_token(raw_key: str) -> str:
    if raw_key in {ALL_GROUP_KEY, READY_GROUP_KEY}:
        return raw_key
    return "c" + hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:8]


def apply_margin(cost: Decimal, margin_percent: Decimal) -> Decimal:
    cost = Decimal(str(cost or 0))
    sell = (cost * (Decimal("1") + margin_percent / Decimal("100"))).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )
    if sell <= 0:
        return Decimal("0.0001")
    return sell


def parse_margin_percent(raw: str) -> Decimal:
    text = (raw or "").strip().replace("%", "").replace("٪", "").replace(",", ".")
    try:
        margin = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("أرسل نسبة الربح كرقم (مثال: 30 أو 30%).") from exc
    if not margin.is_finite() or margin < 0 or margin > 1000:
        raise ValueError("نسبة الربح بين 0 و 1000.")
    return margin.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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
    async def fetch_type(provider, type_key: str) -> list[ProtocolService]:
        protocol = PartnerCatalogService.protocol_for(provider)
        live_type = None if type_key in {"all", "-", ""} else type_key
        services = await protocol.get_services(service_type=live_type)
        services.sort(key=lambda s: (Decimal(str(s.rate or 0)), s.external_id))
        return services

    @staticmethod
    def build_groups(
        services: list[ProtocolService], type_key: str
    ) -> list[tuple[str, str, str, list[ProtocolService]]]:
        """``[(token, emoji, label, services), ...]``"""
        emoji, type_label = partner_type_meta(type_key) if type_key not in {"all", ""} else ("📦", "كل الأنواع")
        groups: list[tuple[str, str, str, list[ProtocolService]]] = []
        ready = [s for s in services if is_ready_number(s)]
        if ready:
            groups.append((READY_GROUP_KEY, "⚡", f"{type_label} الجاهزة", ready))
        cats: dict[str, list[ProtocolService]] = defaultdict(list)
        for svc in services:
            cat = (svc.category or "").strip() or "بدون تصنيف"
            cats[cat].append(svc)
        multi_cat = len(cats) > 1 or (len(cats) == 1 and "بدون تصنيف" not in cats)
        if multi_cat:
            for cat, items in sorted(cats.items(), key=lambda row: (-len(row[1]), row[0])):
                label = cat
                if is_ready_number(items[0]) or any(m in cat.lower() for m in READY_MARKERS):
                    cat_emoji = "⚡"
                else:
                    cat_emoji = "📂"
                groups.append((group_token(cat), cat_emoji, label, items))
        groups.append((ALL_GROUP_KEY, emoji, f"كل {type_label}", list(services)))
        # Drop duplicate lists (ready == a named category).
        seen: set[tuple[str, ...]] = set()
        unique: list[tuple[str, str, str, list[ProtocolService]]] = []
        for token, g_emoji, label, items in groups:
            fingerprint = tuple(s.external_id for s in items)
            if token != ALL_GROUP_KEY and fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append((token, g_emoji, label, items))
        return unique

    @staticmethod
    def resolve_group(
        services: list[ProtocolService], type_key: str, token: str
    ) -> tuple[str, str, str, list[ProtocolService]] | None:
        for group in PartnerCatalogService.build_groups(services, type_key):
            if group[0] == token:
                return group
        return None

    @staticmethod
    async def list_live(
        provider,
        type_key: str,
        page: int = 0,
        per_page: int = SERVICES_PER_PAGE,
    ) -> tuple[list[ProtocolService], int]:
        services = await PartnerCatalogService.fetch_type(provider, type_key)
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
        # التعريب وقت السحب: اسم عربي محفوظ، والأصلي يبقى في name.
        payload = {
            "name": (proto.name or "خدمة")[:500],
            "name_ar": display_service_name(
                proto.name or "", proto.category, proto.service_type
            ),
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
        margin_percent: Decimal | None = None,
    ):
        stored = await PartnerCatalogService.upsert_service(session, provider, proto)
        existing = await PartnerCatalogService._existing_product(
            session, provider.id, proto.external_id, sub_category_id
        )
        # الاسم بالعربية: ما أرسله الأدمن إن أرسل، وإلا تعريب اسم المزود
        default_name = display_service_name(
            proto.name, proto.category, proto.service_type
        )
        if existing is not None:
            PartnerCatalogService._apply_product_fields(
                existing,
                proto,
                stored,
                sell_price,
                name_ar,
                margin_percent,
                default_name=default_name,
            )
            await session.flush()
            return existing
        product = await PulledServicesService.publish(
            session,
            stored,
            sub_category_id,
            sell_price,
            name_ar=name_ar or default_name or proto.name,
        )
        PartnerCatalogService._apply_product_fields(
            product, proto, stored, sell_price, name_ar, margin_percent,
            default_name=default_name,
        )
        await session.flush()
        return product

    @staticmethod
    async def publish_group(
        session,
        provider,
        services: list[ProtocolService],
        sub_category_id: int,
        margin_percent: Decimal,
    ) -> tuple[int, int]:
        created = 0
        updated = 0
        for proto in services:
            stored = await PartnerCatalogService.upsert_service(session, provider, proto)
            cost = Decimal(str(proto.rate or 0))
            sell = apply_margin(cost, margin_percent)
            existing = await PartnerCatalogService._existing_product(
                session, provider.id, proto.external_id, sub_category_id
            )
            if existing is not None:
                PartnerCatalogService._apply_product_fields(
                    existing, proto, stored, sell, proto.name, margin_percent
                )
                updated += 1
                continue
            product = Product(
                sub_category_id=sub_category_id,
                name_ar=(proto.name or "خدمة")[:128],
                description=(proto.description or proto.category or "")[:500] or None,
                price_usd=sell,
                cost_price_usd=cost,
                api_provider_id=provider.id,
                provider_service_id=str(proto.external_id),
                provider_service_ref_id=stored.id,
                fulfillment_type=ProductFulfillmentType.API,
                min_quantity=int(proto.min_quantity or 1),
                max_quantity=int(proto.max_quantity or 1),
                requires_link=bool(proto.requires_link),
                requires_player_id=bool(proto.requires_player_id),
                requires_quantity=bool(proto.requires_quantity),
                display_type=(
                    ProductDisplayType.PER_1000
                    if proto.requires_quantity
                    else ProductDisplayType.FIXED_TOTAL
                ),
                pricing_type=ProductPricingType.MARGIN_PERCENT,
                profit_margin_percent=margin_percent,
                status=ProductStatus.ACTIVE,
            )
            session.add(product)
            created += 1
        await session.flush()
        return created, updated

    @staticmethod
    async def _existing_product(session, provider_id: int, external_id: str, sub_category_id: int):
        result = await session.execute(
            select(Product).where(
                Product.api_provider_id == provider_id,
                Product.provider_service_id == str(external_id),
                Product.sub_category_id == sub_category_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _apply_product_fields(
        product: Product,
        proto: ProtocolService,
        stored: ProviderService,
        sell_price: Decimal,
        name_ar: str | None,
        margin_percent: Decimal | None,
        default_name: str | None = None,
    ) -> None:
        product.name_ar = (name_ar or default_name or proto.name or product.name_ar)[:128]
        product.price_usd = sell_price
        product.cost_price_usd = Decimal(str(proto.rate or 0))
        product.provider_service_ref_id = stored.id
        product.requires_link = bool(proto.requires_link)
        product.requires_quantity = bool(proto.requires_quantity)
        product.display_type = (
            ProductDisplayType.PER_1000
            if proto.requires_quantity
            else ProductDisplayType.FIXED_TOTAL
        )
        product.fulfillment_type = ProductFulfillmentType.API
        product.status = ProductStatus.ACTIVE
        if margin_percent is not None:
            product.pricing_type = ProductPricingType.MARGIN_PERCENT
            product.profit_margin_percent = margin_percent
