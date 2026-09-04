"""
🚀 تحكم الأدمن الكامل بمنتجات قسم الرشق.

الفكرة (كما طلبها الأدمن):

    منتجات قسم الرشق
        └── التطبيقات (إنستغرام، تيك توك، ...) وعدد منتجات كل تطبيق
              └── الأقسام الفرعية داخل التطبيق (متابعون، لايكات، ...)
                    └── منتجات القسم: تعطيل/تفعيل أو حذف أي منتج،
                        وضبط نسبة ربح واحدة لكل منتجات القسم.

هذه الخدمة تحتوي كل الاستعلامات والعمليات الجماعية، وتبقى واجهة
تيليجرام (handlers/admin/smm_products.py) رقيقة قدر الإمكان.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from database.models import (
    Category,
    CategoryType,
    Product,
    ProductPricingType,
    ProductStatus,
    SubCategory,
)
from services.margin_service import MarginService
from services.product_service import ProductService

logger = logging.getLogger(__name__)

PRODUCTS_PER_PAGE = 8


@dataclass
class NodeStats:
    """إحصاء منتجات عقدة (تطبيق أو قسم داخلي)."""

    sub: SubCategory
    direct_total: int = 0
    direct_active: int = 0
    total: int = 0
    active: int = 0
    sections: int = 0

    @property
    def inactive(self) -> int:
        return max(0, self.total - self.active)

    @property
    def label(self) -> str:
        emoji = (self.sub.emoji or "").strip()
        name = (self.sub.name_ar or "").strip() or f"#{self.sub.id}"
        return f"{emoji} {name}".strip()


class SmmAdminService:
    """كل عمليات لوحة «منتجات قسم الرشق»."""

    PRODUCTS_PER_PAGE = PRODUCTS_PER_PAGE

    # ─────────────── الأقسام الرئيسية من نوع رشق ───────────────

    @staticmethod
    async def smm_categories(session) -> list[Category]:
        """كل الأقسام الرئيسية من نوع «رشق» (قد يكون أكثر من واحد)."""
        result = await session.execute(
            select(Category)
            .where(Category.type == CategoryType.SMM)
            .order_by(Category.sort_order, Category.id)
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_sub(session, sub_id: int) -> SubCategory | None:
        result = await session.execute(
            select(SubCategory)
            .options(selectinload(SubCategory.category))
            .where(SubCategory.id == sub_id)
        )
        return result.scalar_one_or_none()

    # ─────────────── إحصاءات المنتجات ───────────────

    @staticmethod
    async def _counts_for_category(session, category_id: int) -> dict[int, tuple[int, int]]:
        """{sub_id: (إجمالي المنتجات، المفعّلة)} لكل أقسام قسم رئيسي."""
        result = await session.execute(
            select(
                Product.sub_category_id,
                func.count(Product.id),
                func.sum(
                    case((Product.status == ProductStatus.ACTIVE, 1), else_=0)
                ),
            )
            .join(SubCategory, SubCategory.id == Product.sub_category_id)
            .where(SubCategory.category_id == category_id)
            .group_by(Product.sub_category_id)
        )
        return {
            int(sub_id): (int(total or 0), int(active or 0))
            for sub_id, total, active in result.all()
        }

    @staticmethod
    async def _subs_of_category(session, category_id: int) -> list[SubCategory]:
        result = await session.execute(
            select(SubCategory)
            .where(SubCategory.category_id == category_id)
            .order_by(SubCategory.sort_order, SubCategory.id)
        )
        return list(result.scalars().all())

    @classmethod
    async def apps_overview(
        cls,
        session,
        category_id: int,
        *,
        only_with_products: bool = True,
    ) -> list[NodeStats]:
        """التطبيقات (الأقسام الجذر) داخل قسم الرشق مع عدد منتجات كل تطبيق.

        العدّ تراكمي: منتجات التطبيق نفسه + منتجات كل أقسامه الداخلية.
        """
        subs = await cls._subs_of_category(session, category_id)
        counts = await cls._counts_for_category(session, category_id)

        children: dict[int, list[SubCategory]] = {}
        roots: list[SubCategory] = []
        for sub in subs:
            if sub.parent_sub_category_id:
                children.setdefault(sub.parent_sub_category_id, []).append(sub)
            else:
                roots.append(sub)

        rows: list[NodeStats] = []
        for root in roots:
            direct_total, direct_active = counts.get(root.id, (0, 0))
            stats = NodeStats(
                sub=root,
                direct_total=direct_total,
                direct_active=direct_active,
                total=direct_total,
                active=direct_active,
            )
            stack = list(children.get(root.id, []))
            stats.sections = len(stack)
            seen: set[int] = {root.id}
            while stack:
                node = stack.pop()
                if node.id in seen:
                    continue
                seen.add(node.id)
                total, active = counts.get(node.id, (0, 0))
                stats.total += total
                stats.active += active
                stack.extend(children.get(node.id, []))
            rows.append(stats)

        if only_with_products:
            rows = [row for row in rows if row.total > 0]
        rows.sort(key=lambda row: (-row.total, row.sub.sort_order, row.sub.id))
        return rows

    @classmethod
    async def sections_overview(cls, session, app_id: int) -> list[NodeStats]:
        """الأقسام الداخلية لتطبيق واحد مع عدد منتجات كل قسم."""
        app = await cls.get_sub(session, app_id)
        if app is None:
            return []
        counts = await cls._counts_for_category(session, app.category_id)
        result = await session.execute(
            select(SubCategory)
            .where(SubCategory.parent_sub_category_id == app_id)
            .order_by(SubCategory.sort_order, SubCategory.id)
        )
        rows: list[NodeStats] = []
        for sub in result.scalars().all():
            total, active = counts.get(sub.id, (0, 0))
            rows.append(
                NodeStats(
                    sub=sub,
                    direct_total=total,
                    direct_active=active,
                    total=total,
                    active=active,
                )
            )
        return rows

    @classmethod
    async def node_stats(cls, session, sub_id: int) -> NodeStats | None:
        """إحصاء قسم واحد (منتجاته المباشرة + شجرته)."""
        sub = await cls.get_sub(session, sub_id)
        if sub is None:
            return None
        counts = await cls._counts_for_category(session, sub.category_id)
        tree_ids = await ProductService.subcategory_tree_ids(session, sub_id)
        direct_total, direct_active = counts.get(sub_id, (0, 0))
        stats = NodeStats(
            sub=sub,
            direct_total=direct_total,
            direct_active=direct_active,
        )
        for node_id in tree_ids:
            total, active = counts.get(node_id, (0, 0))
            stats.total += total
            stats.active += active
        stats.sections = max(0, len(tree_ids) - 1)
        return stats

    # ─────────────── منتجات قسم واحد (صفحات) ───────────────

    @classmethod
    async def products_page(
        cls,
        session,
        sub_id: int,
        page: int = 0,
        per_page: int = PRODUCTS_PER_PAGE,
    ) -> tuple[list[Product], int, int]:
        """منتجات القسم المباشرة: (منتجات الصفحة، الإجمالي، عدد الصفحات).

        تُحمَّل بيانات المزود وخدمته معها حتى تُعرض تكلفة المزود وآيدي
        الخدمة عنده بجانب سعرنا في البوت.
        """
        total = int(
            (
                await session.execute(
                    select(func.count(Product.id)).where(
                        Product.sub_category_id == sub_id
                    )
                )
            ).scalar_one()
            or 0
        )
        pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, pages - 1))
        result = await session.execute(
            select(Product)
            .options(
                selectinload(Product.api_provider),
                selectinload(Product.provider_service),
            )
            .where(Product.sub_category_id == sub_id)
            .order_by(Product.sort_order, Product.price_usd, Product.id)
            .offset(page * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), total, pages

    # ─────────── المنتجات بلا سعر (خدمات «سيرفر» الصفرية) ───────────

    @classmethod
    async def unpriced_count(
        cls, session, sub_id: int, *, include_children: bool = True
    ) -> int:
        """عدد المنتجات بلا سعر بيع (0$) — خدمات المزود غير المسعّرة."""
        ids = await cls._tree_ids(session, sub_id, include_children=include_children)
        if not ids:
            return 0
        result = await session.execute(
            select(func.count(Product.id)).where(
                Product.sub_category_id.in_(ids),
                Product.price_usd <= 0,
            )
        )
        return int(result.scalar_one() or 0)

    @classmethod
    async def delete_unpriced(
        cls, session, sub_id: int, *, include_children: bool = True
    ) -> int:
        """يحذف المنتجات بلا سعر بيع (مخلّفات سحب قديم لخدمات صفرية)."""
        ids = await cls._tree_ids(session, sub_id, include_children=include_children)
        if not ids:
            return 0
        result = await session.execute(
            select(Product).where(
                Product.sub_category_id.in_(ids),
                Product.price_usd <= 0,
            )
        )
        deleted = 0
        for product in result.scalars().all():
            await session.delete(product)
            deleted += 1
        try:
            await session.commit()
        except Exception:
            logger.exception("فشل حذف منتجات الرشق بلا سعر")
            await session.rollback()
            return 0
        return deleted

    @staticmethod
    def provider_info(product: Product) -> dict:
        """معلومات المنتج عند المزود: الاسم، آيدي الخدمة، سعر المزود، الربح."""
        service = getattr(product, "provider_service", None)
        provider = getattr(product, "api_provider", None)
        cost = Decimal(str(product.cost_price_usd or 0))
        # سعر المزود اللحظي إن توفّر (قد يتغيّر بعد آخر مزامنة).
        live_cost = None
        if service is not None:
            try:
                live_cost = Decimal(str(service.rate_usd or 0))
            except Exception:
                live_cost = None
        sell = Decimal(str(product.price_usd or 0))
        profit = sell - cost
        margin = None
        if cost > 0:
            margin = ((sell / cost - Decimal("1")) * Decimal("100")).quantize(
                Decimal("0.01")
            )
        return {
            "provider_name": getattr(provider, "name", None),
            "service_id": product.provider_service_id
            or (getattr(service, "external_service_id", None)),
            "cost": cost,
            "live_cost": live_cost,
            "sell": sell,
            "profit": profit,
            "margin": margin,
            "min_quantity": product.min_quantity,
            "max_quantity": product.max_quantity,
            "stale_cost": (
                live_cost is not None and live_cost > 0 and live_cost != cost
            ),
            "unpriced": sell <= 0 or cost <= 0,
        }

    # ─────────────── عمليات على منتج واحد ───────────────

    @staticmethod
    async def toggle_product(session, product_id: int) -> Product | None:
        """يعطّل المنتج المفعّل ويفعّل المعطّل."""
        product = await session.get(Product, product_id)
        if product is None:
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
    async def delete_product(session, product_id: int) -> bool:
        product = await session.get(Product, product_id)
        if product is None:
            return False
        await session.delete(product)
        try:
            await session.commit()
        except Exception:
            logger.exception("فشل حذف منتج الرشق %s", product_id)
            await session.rollback()
            return False
        return True

    # ─────────────── عمليات جماعية على قسم ───────────────

    @staticmethod
    async def _tree_ids(session, sub_id: int, *, include_children: bool) -> list[int]:
        if not include_children:
            return [sub_id]
        return await ProductService.subcategory_tree_ids(session, sub_id)

    @classmethod
    async def bulk_set_status(
        cls,
        session,
        sub_id: int,
        *,
        active: bool,
        include_children: bool = True,
    ) -> int:
        """يفعّل/يعطّل كل منتجات القسم (وأقسامه الداخلية). يرجع عدد المتغيّر."""
        ids = await cls._tree_ids(session, sub_id, include_children=include_children)
        if not ids:
            return 0
        target = ProductStatus.ACTIVE if active else ProductStatus.INACTIVE
        result = await session.execute(
            select(Product).where(
                Product.sub_category_id.in_(ids),
                Product.status != target,
            )
        )
        changed = 0
        for product in result.scalars().all():
            product.status = target
            changed += 1
        await session.commit()
        return changed

    @classmethod
    async def delete_section_products(
        cls,
        session,
        sub_id: int,
        *,
        include_children: bool = True,
    ) -> tuple[int, int]:
        """يحذف كل منتجات القسم — الأقسام نفسها تبقى."""
        ids = await cls._tree_ids(session, sub_id, include_children=include_children)
        return await ProductService.delete_products_by_subcategory_ids(session, ids)

    # ─────────────── نسبة الربح لكل منتجات القسم ───────────────

    @classmethod
    async def apply_margin(
        cls,
        session,
        sub: SubCategory,
        percent: Decimal | None,
        *,
        include_children: bool = True,
    ) -> dict:
        """يضبط نسبة ربح القسم ويطبّقها على كل منتجاته فوراً.

        - ``percent`` رقم: يُحفظ على القسم ويُختم على كل منتج فيه
          (حتى المنتجات التي كان لها هامش يدوي — القسم هو المرجع الآن)،
          ويُعاد حساب السعر = التكلفة × (1 + النسبة/100).
        - ``percent = None``: يُلغى هامش القسم وهوامش منتجاته الخاصة،
          فترث الهامش الأعلى (القسم الرئيسي ثم العالمي) ويُعاد الحساب.

        يرجع تقريراً: {'percent', 'products', 'repriced', 'no_cost', 'sections'}
        """
        percent = MarginService._clamp(percent)
        sub.profit_margin_percent = percent

        ids = await cls._tree_ids(session, sub.id, include_children=include_children)
        result = await session.execute(
            select(Product).where(Product.sub_category_id.in_(ids))
        )
        products = list(result.scalars().all())

        repriced = 0
        no_cost = 0
        for product in products:
            cost = Decimal(str(product.cost_price_usd or 0))
            if percent is None:
                # إلغاء: المنتج يعود ليرث الهامش الأعلى.
                product.profit_margin_percent = None
                product.margin_manual = False
                if cost <= 0:
                    no_cost += 1
                    continue
                effective, _source = await MarginService.resolve_product_margin(
                    session, product
                )
                new_price = MarginService.price_from_cost(cost, Decimal(str(effective)))
                if new_price != Decimal(str(product.price_usd)):
                    product.price_usd = new_price
                    repriced += 1
                continue

            product.profit_margin_percent = percent
            product.pricing_type = ProductPricingType.MARGIN_PERCENT
            # الهامش صار محكوماً بالقسم لا بالمنتج → لا نقفله يدوياً،
            # حتى يبقى تغيير هامش القسم لاحقاً نافذاً على الجميع.
            product.margin_manual = False
            if cost <= 0:
                no_cost += 1
                continue
            new_price = MarginService.price_from_cost(cost, percent)
            if new_price != Decimal(str(product.price_usd)):
                product.price_usd = new_price
                repriced += 1

        await session.commit()
        return {
            "percent": percent,
            "products": len(products),
            "repriced": repriced,
            "no_cost": no_cost,
            "sections": len(ids),
        }

    @classmethod
    async def effective_margin_text(cls, session, sub: SubCategory) -> str:
        """وصف نصي لهامش القسم الحالي (خاص أو موروث)."""
        if sub.profit_margin_percent is not None:
            return f"{Decimal(str(sub.profit_margin_percent))}% (خاص بهذا القسم)"
        percent, source = await MarginService.resolve_sub_margin(session, sub)
        return f"{Decimal(str(percent))}% (موروث من {source})"
