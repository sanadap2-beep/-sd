"""
خدمة إدارة المنتجات.

المسؤوليات:
1) إنشاء منتج من خدمة مزود
2) حساب سعر البيع (ثابت / نسبة ربح)
3) عرض السعر للمستخدم بطرق مختلفة
4) تحديث المنتجات
5) إحصائيات المبيعات
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select, func, desc, and_, or_
from sqlalchemy.orm import selectinload

from database.models import (
    Product,
    ProductStatus,
    ProductFulfillmentType,
    ProductPricingType,
    ProductDisplayType,
    ProviderService,
    SubCategory,
    UnifiedOrder,
    UnifiedOrderStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class PriceCalculation:
    """نتيجة حساب السعر."""

    cost_price_usd: Decimal
    sell_price_usd: Decimal
    profit_usd: Decimal
    profit_percent: Decimal
    display_text: str


class ProductService:
    """خدمة إدارة المنتجات."""

    # ══════════════════════════════════════════
    # ══════════════ حساب الأسعار ══════════════
    # ══════════════════════════════════════════

    @staticmethod
    def calculate_sell_price(
        cost_price_usd: Decimal,
        pricing_type: ProductPricingType,
        fixed_price: Decimal | None = None,
        margin_percent: Decimal | None = None,
    ) -> Decimal:
        """
        يحسب سعر البيع النهائي.

        Args:
            cost_price_usd: سعر التكلفة من المزود
            pricing_type: نوع التسعير (ثابت / نسبة)
            fixed_price: السعر الثابت (إذا كان النوع FIXED)
            margin_percent: نسبة الربح (إذا كان النوع MARGIN_PERCENT)

        Returns:
            سعر البيع النهائي بالدولار
        """
        if pricing_type == ProductPricingType.FIXED:
            if fixed_price is None:
                raise ValueError("fixed_price مطلوب مع نوع FIXED")
            return fixed_price.quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )

        if pricing_type == ProductPricingType.MARGIN_PERCENT:
            if margin_percent is None:
                raise ValueError("margin_percent مطلوب مع نوع MARGIN_PERCENT")
            multiplier = Decimal("1") + margin_percent / Decimal("100")
            sell = cost_price_usd * multiplier
            return sell.quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )

        raise ValueError(f"نوع تسعير غير معروف: {pricing_type}")

    @staticmethod
    def calculate_profit(
        cost_price_usd: Decimal,
        sell_price_usd: Decimal,
    ) -> tuple[Decimal, Decimal]:
        """
        يحسب الربح والنسبة المئوية.

        Returns:
            (profit_usd, profit_percent)
        """
        profit = sell_price_usd - cost_price_usd
        percent = Decimal("0")

        if cost_price_usd > 0:
            percent = (profit / cost_price_usd * Decimal("100")).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )

        return (
            profit.quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            ),
            percent,
        )

    # ══════════════════════════════════════════
    # ══════════════ عرض السعر للمستخدم ══════════════
    # ══════════════════════════════════════════

    @staticmethod
    def format_price_display(
        price_usd: Decimal,
        display_type: ProductDisplayType,
        min_quantity: int = 1,
    ) -> str:
        """
        يحول السعر لنص قابل للعرض للمستخدم.

        Args:
            price_usd: السعر بالدولار
            display_type: طريقة العرض
            min_quantity: الحد الأدنى (يُستخدم مع per_min_quantity)

        Returns:
            نص السعر للعرض
        """
        if display_type == ProductDisplayType.PER_1000:
            return f"{price_usd}$ / 1000"

        if display_type == ProductDisplayType.PER_MIN_QUANTITY:
            return f"{price_usd}$ / {min_quantity}"

        if display_type == ProductDisplayType.FIXED_TOTAL:
            return f"{price_usd}$ للطلب"

        return f"{price_usd}$"

    @staticmethod
    def calculate_order_total(
        product: Product,
        quantity: int,
    ) -> Decimal:
        """
        يحسب السعر الإجمالي للطلب بناءً على الكمية ونوع العرض.

        لقسم الرشق (requires_quantity): السعر المخزّن هو سعر الكمية 1000
        ما لم يُضبط العرض صراحةً على per_min_quantity أو ثابت.
        """
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 1
        if quantity < 1:
            quantity = 1

        price = Decimal(str(product.price_usd or 0))
        if not getattr(product, "requires_quantity", False):
            return price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        display = getattr(product, "display_type", ProductDisplayType.PER_1000)
        if display == ProductDisplayType.PER_MIN_QUANTITY:
            min_qty = int(product.min_quantity or 0)
            if min_qty <= 0:
                return price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            total = price * Decimal(str(quantity)) / Decimal(str(min_qty))
        elif display == ProductDisplayType.FIXED_TOTAL:
            total = price
        else:
            # PER_1000 — سعر الأدمن هو للكمية 1000 وليس للحد الأدنى (غالباً 100).
            total = price * Decimal(str(quantity)) / Decimal("1000")

        return total.quantize(
            Decimal("0.0001"),
            rounding=ROUND_HALF_UP,
        )

    # ══════════════════════════════════════════
    # ══════════════ CRUD المنتجات ══════════════
    # ══════════════════════════════════════════

    @staticmethod
    async def create_from_provider_service(
        session,
        sub_category_id: int,
        provider_service_id: int,
        name_ar: str,
        pricing_type: ProductPricingType,
        fixed_price: Decimal | None = None,
        margin_percent: Decimal | None = None,
        display_type: ProductDisplayType = ProductDisplayType.PER_1000,
        description: str | None = None,
        image_file_id: str | None = None,
        image_url: str | None = None,
        min_quantity_override: int | None = None,
        max_quantity_override: int | None = None,
        sort_order: int = 0,
    ) -> Product:
        """
        ينشئ منتج جديد من خدمة مزود موجودة.

        هذه الدالة الرئيسية لـ Wizard إنشاء المنتج.
        """
        provider_service = await session.get(ProviderService, provider_service_id)
        if not provider_service:
            raise ValueError(f"خدمة المزود {provider_service_id} غير موجودة")

        sub_category = await session.get(SubCategory, sub_category_id)
        if not sub_category:
            raise ValueError(f"القسم الفرعي {sub_category_id} غير موجود")

        cost_price = provider_service.rate_usd

        sell_price = ProductService.calculate_sell_price(
            cost_price_usd=cost_price,
            pricing_type=pricing_type,
            fixed_price=fixed_price,
            margin_percent=margin_percent,
        )

        min_qty = (
            min_quantity_override
            if min_quantity_override is not None
            else provider_service.min_quantity
        )
        max_qty = (
            max_quantity_override
            if max_quantity_override is not None
            else provider_service.max_quantity
        )

        product = Product(
            sub_category_id=sub_category_id,
            api_provider_id=provider_service.api_provider_id,
            provider_service_ref_id=provider_service.id,
            provider_service_id=(provider_service.external_service_id),
            name_ar=name_ar,
            description=description,
            image_url=image_url,
            image_file_id=image_file_id,
            price_usd=sell_price,
            cost_price_usd=cost_price,
            pricing_type=pricing_type,
            profit_margin_percent=margin_percent,
            display_type=display_type,
            min_quantity=min_qty,
            max_quantity=max_qty,
            requires_link=provider_service.requires_link,
            requires_quantity=(provider_service.requires_quantity),
            requires_player_id=(provider_service.requires_player_id),
            status=ProductStatus.ACTIVE,
            sort_order=sort_order,
        )

        session.add(product)
        await session.commit()
        await session.refresh(product)

        logger.info(f"تم إنشاء منتج جديد #{product.id}: {product.name_ar} - {product.price_usd}$")

        return product

    @staticmethod
    async def create_manual(
        session,
        sub_category_id: int,
        name_ar: str,
        price_usd: Decimal,
        description: str | None = None,
        image_file_id: str | None = None,
        image_url: str | None = None,
        min_quantity: int = 1,
        max_quantity: int = 1,
        requires_link: bool = False,
        requires_quantity: bool = False,
        requires_player_id: bool = False,
        display_type: ProductDisplayType = ProductDisplayType.FIXED_TOTAL,
        sort_order: int = 0,
    ) -> Product:
        """ينشئ منتج يدوي (بدون ربط بخدمة مزود)."""
        product = Product(
            sub_category_id=sub_category_id,
            name_ar=name_ar,
            description=description,
            image_url=image_url,
            image_file_id=image_file_id,
            price_usd=price_usd,
            cost_price_usd=Decimal("0"),
            pricing_type=ProductPricingType.FIXED,
            fulfillment_type=ProductFulfillmentType.MANUAL,
            display_type=display_type,
            min_quantity=min_quantity,
            max_quantity=max_quantity,
            requires_link=requires_link,
            requires_quantity=requires_quantity,
            requires_player_id=requires_player_id,
            status=ProductStatus.ACTIVE,
            sort_order=sort_order,
        )

        session.add(product)
        await session.commit()
        await session.refresh(product)

        logger.info(f"تم إنشاء منتج يدوي #{product.id}: {product.name_ar}")
        return product

    @staticmethod
    async def get_product(session, product_id: int) -> Product | None:
        """يجلب منتج مع كل علاقاته."""
        result = await session.execute(
            select(Product)
            .options(
                selectinload(Product.sub_category).selectinload(SubCategory.category),
                selectinload(Product.api_provider),
                selectinload(Product.provider_service),
            )
            .where(Product.id == product_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_products_by_sub_category(
        session,
        sub_category_id: int,
        active_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Product]:
        """يجلب كل منتجات قسم فرعي."""
        query = (
            select(Product)
            .options(selectinload(Product.api_provider))
            .where(Product.sub_category_id == sub_category_id)
        )

        if active_only:
            query = query.where(Product.status == ProductStatus.ACTIVE)

        query = query.order_by(Product.sort_order, Product.id)

        if limit:
            query = query.limit(limit).offset(offset)

        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def count_products_by_sub_category(
        session,
        sub_category_id: int,
        active_only: bool = False,
    ) -> int:
        """يحصي عدد منتجات قسم فرعي."""
        query = select(func.count(Product.id)).where(Product.sub_category_id == sub_category_id)

        if active_only:
            query = query.where(Product.status == ProductStatus.ACTIVE)

        result = await session.execute(query)
        return result.scalar_one()

    @staticmethod
    async def update_product(
        session,
        product_id: int,
        **fields: Any,
    ) -> Product | None:
        """
        يحدّث حقول منتج.

        الحقول المدعومة:
        - name_ar, description, image_file_id, image_url
        - price_usd, pricing_type, profit_margin_percent
        - display_type, min_quantity, max_quantity
        - sort_order, status, is_featured, is_bestseller
        - requires_link, requires_quantity, requires_player_id
        """
        product = await session.get(Product, product_id)
        if not product:
            return None

        allowed_fields = {
            "name_ar",
            "description",
            "image_file_id",
            "image_url",
            "price_usd",
            "cost_price_usd",
            "pricing_type",
            "profit_margin_percent",
            "display_type",
            "min_quantity",
            "max_quantity",
            "sort_order",
            "status",
            "is_featured",
            "is_bestseller",
            "requires_link",
            "requires_quantity",
            "requires_player_id",
        }

        for key, value in fields.items():
            if key in allowed_fields:
                setattr(product, key, value)

        await session.commit()
        await session.refresh(product)
        return product

    @staticmethod
    async def toggle_status(session, product_id: int) -> Product | None:
        """يبدل حالة منتج (نشط/معطّل)."""
        product = await session.get(Product, product_id)
        if not product:
            return None

        product.status = (
            ProductStatus.INACTIVE
            if product.status == ProductStatus.ACTIVE
            else ProductStatus.ACTIVE
        )

        await session.commit()
        await session.refresh(product)
        return product

    @staticmethod
    async def toggle_featured(session, product_id: int) -> Product | None:
        """يبدل حالة "مميز" لمنتج."""
        product = await session.get(Product, product_id)
        if not product:
            return None

        product.is_featured = not product.is_featured
        await session.commit()
        await session.refresh(product)
        return product

    @staticmethod
    async def toggle_bestseller(session, product_id: int) -> Product | None:
        """يبدل حالة "الأكثر مبيعاً" لمنتج."""
        product = await session.get(Product, product_id)
        if not product:
            return None

        product.is_bestseller = not product.is_bestseller
        await session.commit()
        await session.refresh(product)
        return product

    @staticmethod
    async def delete_product(session, product_id: int) -> bool:
        """يحذف منتج."""
        product = await session.get(Product, product_id)
        if not product:
            return False

        await session.delete(product)
        await session.commit()
        return True

    # ══════════════════════════════════════════
    # ══════════════ إحصائيات ══════════════
    # ══════════════════════════════════════════

    @staticmethod
    async def get_product_stats(session, product_id: int) -> dict:
        """يجلب إحصائيات منتج."""
        product = await session.get(Product, product_id)
        if not product:
            return {}

        total_orders = await session.execute(
            select(func.count(UnifiedOrder.id)).where(UnifiedOrder.product_id == product_id)
        )
        total_orders_count = total_orders.scalar_one()

        completed = await session.execute(
            select(func.count(UnifiedOrder.id)).where(
                and_(
                    UnifiedOrder.product_id == product_id,
                    UnifiedOrder.status == UnifiedOrderStatus.COMPLETED,
                )
            )
        )
        completed_count = completed.scalar_one()

        total_revenue = await session.execute(
            select(
                func.coalesce(
                    func.sum(UnifiedOrder.price_usd),
                    Decimal("0"),
                )
            ).where(
                and_(
                    UnifiedOrder.product_id == product_id,
                    UnifiedOrder.status == UnifiedOrderStatus.COMPLETED,
                )
            )
        )
        revenue = total_revenue.scalar_one()

        total_cost = await session.execute(
            select(
                func.coalesce(
                    func.sum(UnifiedOrder.cost_price_usd),
                    Decimal("0"),
                )
            ).where(
                and_(
                    UnifiedOrder.product_id == product_id,
                    UnifiedOrder.status == UnifiedOrderStatus.COMPLETED,
                )
            )
        )
        cost = total_cost.scalar_one()

        return {
            "total_orders": total_orders_count,
            "completed_orders": completed_count,
            "total_revenue_usd": revenue,
            "total_cost_usd": cost,
            "total_profit_usd": revenue - cost,
            "views": product.view_count,
            "sold": product.total_sold,
        }

    @staticmethod
    async def get_bestsellers(
        session,
        limit: int = 10,
        active_only: bool = True,
    ) -> list[Product]:
        """يجلب أكثر المنتجات مبيعاً."""
        query = select(Product).options(selectinload(Product.sub_category))

        if active_only:
            query = query.where(Product.status == ProductStatus.ACTIVE)

        query = query.order_by(desc(Product.total_sold)).limit(limit)

        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def get_featured(
        session,
        limit: int = 10,
        active_only: bool = True,
    ) -> list[Product]:
        """يجلب المنتجات المميزة."""
        query = (
            select(Product)
            .options(selectinload(Product.sub_category))
            .where(Product.is_featured.is_(True))
        )

        if active_only:
            query = query.where(Product.status == ProductStatus.ACTIVE)

        query = query.order_by(Product.sort_order, Product.id).limit(limit)

        result = await session.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def increment_views(session, product_id: int) -> None:
        """يزيد عداد مشاهدات المنتج."""
        product = await session.get(Product, product_id)
        if product:
            product.view_count = (product.view_count or 0) + 1
            await session.commit()

    @staticmethod
    async def increment_sold(session, product_id: int, quantity: int = 1) -> None:
        """يزيد عداد مبيعات المنتج."""
        product = await session.get(Product, product_id)
        if product:
            product.total_sold = (product.total_sold or 0) + quantity
            await session.commit()

    @staticmethod
    async def search_products(
        session,
        query_text: str,
        limit: int = 20,
        active_only: bool = True,
    ) -> list[Product]:
        """يبحث في المنتجات بالاسم أو الوصف."""
        if not query_text or not query_text.strip():
            return []

        search_pattern = f"%{query_text.strip()}%"

        query = (
            select(Product)
            .options(selectinload(Product.sub_category))
            .where(
                or_(
                    Product.name_ar.ilike(search_pattern),
                    Product.description.ilike(search_pattern),
                )
            )
        )

        if active_only:
            query = query.where(Product.status == ProductStatus.ACTIVE)

        query = query.limit(limit)

        result = await session.execute(query)
        return list(result.scalars().all())
