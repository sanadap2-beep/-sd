"""Universal storefront discovery for all non-number products.

This service is intentionally category-agnostic: games, SMM, apps, digital
inventory, subscriptions, and any future product type are discovered from the
same products table instead of building another numbers-only shortcut.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import Category, CategoryType, Product, ProductFulfillmentType, ProductStatus, SubCategory
from services.feature_service import FeatureService
from services.inventory_service import InventoryService
from services.promotion_service import PromotionService


class StoreDiscoveryService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("full_store_hub")

    @staticmethod
    async def section_limit() -> int:
        return max(3, await FeatureService.config_int("full_store_hub", "section_limit", 8))

    @staticmethod
    async def overview(session) -> dict:
        """Return counts and spotlight sections for the whole store."""
        categories = list(
            (
                await session.execute(
                    select(Category)
                    .options(selectinload(Category.sub_categories))
                    .where(Category.is_active.is_(True))
                    .order_by(Category.sort_order, Category.id)
                )
            )
            .scalars()
            .unique()
            .all()
        )
        products = await StoreDiscoveryService.products(session, "all", limit=200)
        type_counts: dict[str, int] = {kind.value: 0 for kind in CategoryType}
        for product in products:
            category = product.sub_category.category if product.sub_category else None
            if category:
                type_counts[category.type.value] = type_counts.get(category.type.value, 0) + 1
        return {
            "categories": categories,
            "total_products": len(products),
            "type_counts": type_counts,
            "featured": await StoreDiscoveryService.products(session, "featured"),
            "deals": await StoreDiscoveryService.products(session, "deals"),
            "instant": await StoreDiscoveryService.products(session, "instant"),
        }

    @staticmethod
    async def products(session, section: str, limit: int | None = None) -> list[Product]:
        """Products for a storefront section."""
        limit = limit or await StoreDiscoveryService.section_limit()
        base = (
            select(Product)
            .options(selectinload(Product.sub_category).selectinload(SubCategory.category))
            .where(Product.status == ProductStatus.ACTIVE)
        )

        if section == "featured":
            query = base.where(Product.is_featured.is_(True)).order_by(Product.sort_order, Product.id)
        elif section == "bestsellers":
            query = base.order_by(Product.total_sold.desc(), Product.id)
        elif section == "cheap":
            max_price = Decimal(str(await FeatureService.config_decimal("full_store_hub", "cheap_max_usd", 2.0)))
            query = base.where(Product.price_usd <= max_price).order_by(Product.price_usd, Product.id)
        elif section == "instant":
            query = base.where(Product.fulfillment_type == ProductFulfillmentType.INVENTORY).order_by(
                Product.sort_order, Product.id
            )
        elif section == "games":
            query = base.join(Product.sub_category).join(SubCategory.category).where(
                Category.type == CategoryType.GAMES
            ).order_by(Product.total_sold.desc(), Product.id)
        elif section == "smm":
            query = base.join(Product.sub_category).join(SubCategory.category).where(
                Category.type == CategoryType.SMM
            ).order_by(Product.total_sold.desc(), Product.id)
        elif section == "apps":
            query = base.join(Product.sub_category).join(SubCategory.category).where(
                Category.type == CategoryType.APPS
            ).order_by(Product.total_sold.desc(), Product.id)
        elif section == "deals":
            promotions = await PromotionService.get_active_promotions(session, limit=limit)
            return [promo.product for promo in promotions if promo.product and promo.product.status == ProductStatus.ACTIVE]
        else:
            query = base.order_by(Product.sort_order, Product.id)

        return list((await session.execute(query.limit(limit))).scalars().unique().all())

    @staticmethod
    async def instant_stock_count(session, product: Product) -> int | None:
        if product.fulfillment_type != ProductFulfillmentType.INVENTORY:
            return None
        return await InventoryService.available_count(session, product.id)
