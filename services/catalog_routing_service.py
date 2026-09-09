"""
توجيه المنتج بين المزودين مع Failover.

المشكلة التي يحلها هذا الملف:
كل منتج مربوط بمزود **واحد** عبر `Product.api_provider_id`. فإن تعطّل
ذلك المزود أو نفدت خدمته، يظهر للمستخدم «هذا المنتج غير جاهز للطلب»
رغم أن نفس الخدمة متوفرة عند مزودين آخرين مسجّلين في النظام.

الحل: قائمة مسارات مرتبة لكل منتج.
1) المسار الأساسي من Product نفسه.
2) المسارات الاحتياطية من product_provider_routes مرتبة بـ priority.
يجرَّب كل مسار بدوره حتى ينجح أحدها، فتصير جهوزية الكتالوج مستقلة
عن مزود واحد.

الميزة قابلة للإيقاف من مركز الإضافات، وعندها يعود السلوك القديم
تماماً (مسار واحد بلا احتياط).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select

from database.models import ApiProvider, Product, ProductProviderRoute
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Route:
    """مزود واحد قادر على تنفيذ منتج، مع معرف الخدمة عنده."""

    api_provider_id: int
    provider_service_id: str
    priority: int
    is_primary: bool


class CatalogRoutingService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("catalog_failover")

    @staticmethod
    async def routes_for(session, product: Product) -> list[Route]:
        """
        كل المسارات القابلة للاستخدام لمنتج، مرتبة بالأولوية.

        المسار الأساسي أول دائماً ما لم يحدده الأدمن خلاف ذلك، لأنه
        المزود الذي ضُبِط سعر المنتج على أساسه.
        """
        routes: list[Route] = []

        # لا نستخدم `product.api_provider` هنا: الوصول إلى علاقة SQLAlchemy
        # غير محمّلة يُطلق lazy load متزامن داخل سياق async فيرمي
        # MissingGreenlet. نجلب المزود صراحةً باستعلام غير متزامن-آمن.
        if product.api_provider_id and product.provider_service_id:
            provider = await session.get(ApiProvider, product.api_provider_id)
            if provider is not None and provider.is_active:
                routes.append(
                    Route(
                        api_provider_id=provider.id,
                        provider_service_id=product.provider_service_id,
                        priority=0,
                        is_primary=True,
                    )
                )

        if not await CatalogRoutingService.enabled():
            return routes

        result = await session.execute(
            select(ProductProviderRoute).where(
                ProductProviderRoute.product_id == product.id,
                ProductProviderRoute.is_active.is_(True),
            )
        )
        for route in result.scalars().all():
            provider = await session.get(ApiProvider, route.api_provider_id)
            if provider is None or not provider.is_active:
                continue
            if route.api_provider_id == product.api_provider_id:
                continue  # مكرر مع الأساسي
            routes.append(
                Route(
                    api_provider_id=route.api_provider_id,
                    provider_service_id=route.provider_service_id,
                    priority=route.priority,
                    is_primary=False,
                )
            )

        routes.sort(key=lambda r: (r.priority, 0 if r.is_primary else 1))
        return routes

    @staticmethod
    async def add_route(
        session,
        product_id: int,
        api_provider_id: int,
        provider_service_id: str,
        priority: int = 100,
    ) -> ProductProviderRoute:
        """يضيف مساراً احتياطياً أو يحدّث الموجود."""
        result = await session.execute(
            select(ProductProviderRoute).where(
                ProductProviderRoute.product_id == product_id,
                ProductProviderRoute.api_provider_id == api_provider_id,
            )
        )
        route = result.scalar_one_or_none()
        if route is None:
            route = ProductProviderRoute(
                product_id=product_id,
                api_provider_id=api_provider_id,
                provider_service_id=provider_service_id,
                priority=priority,
            )
            session.add(route)
        else:
            route.provider_service_id = provider_service_id
            route.priority = priority
            route.is_active = True
        await session.commit()
        await session.refresh(route)
        return route

    @staticmethod
    async def remove_route(session, route_id: int) -> bool:
        route = await session.get(ProductProviderRoute, route_id)
        if route is None:
            return False
        await session.delete(route)
        await session.commit()
        return True

    @staticmethod
    async def coverage_report(session) -> dict:
        """كم منتجاً صار له أكثر من مزود — مقياس جهوزية الكتالوج.
        
        يُحسَّن بعدم جلب كل الـ ids دفعة واحدة ثم iterate.
        """
        from database.models import ProductStatus

        rows = (
            await session.execute(
                select(Product.id, Product.api_provider_id).where(
                    Product.status == ProductStatus.ACTIVE,
                    Product.api_provider_id.is_not(None),
                )
            )
        ).all()
        total = len(rows)
        covered = 0
        for product_id, _ in rows:
            product = await session.get(Product, product_id)
            if product is None:
                continue
            if len(await CatalogRoutingService.routes_for(session, product)) > 1:
                covered += 1
        return {
            "products_with_provider": total,
            "products_with_failover": covered,
            "coverage_percent": round(covered / total * 100, 1) if total else 0.0,
        }
