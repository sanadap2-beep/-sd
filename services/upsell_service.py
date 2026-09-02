"""Complementary product recommendations for checkout and catalog surfaces."""

from __future__ import annotations

from sqlalchemy import desc, select

from database.models import Product, ProductStatus


class UpsellService:
    @staticmethod
    async def recommend(session, product_id: int, limit: int = 3) -> list[Product]:
        source = await session.get(Product, product_id)
        if source is None:
            return []
        result = await session.execute(
            select(Product)
            .where(
                Product.id != product_id,
                Product.status == ProductStatus.ACTIVE,
                Product.sub_category_id == source.sub_category_id,
            )
            .order_by(
                desc(Product.is_featured),
                desc(Product.is_bestseller),
                desc(Product.total_sold),
                Product.id,
            )
            .limit(max(1, min(limit, 10)))
        )
        return list(result.scalars().all())

    @staticmethod
    def format_for_message(products: list[Product]) -> str:
        if not products:
            return ""
        return "\n".join(f"• {product.name_ar} — {product.price_usd}$" for product in products)
