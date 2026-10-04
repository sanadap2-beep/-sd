"""Mirror purchases for white-label sub-bots.

التدفق (ترتيب آمن — لا حالة ضياع):
1) حساب السعر الأساسي (سعر البوت) وسعر التاجر (أساسي + هامش) والعمولة.
2) خصم المحفظة أولاً (أساسي + عمولة) — قابلة للاسترداد دائماً عبر fund().
3) خصم الزبون الفرعي (سعر التاجر) — قابلة للاسترداد عبر add_balance.
4) تنفيذ المزود/المخزون — عند الفشل يُسترد الجانبان.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import (
    ApiProvider,
    Product,
    ProductFulfillmentType,
    ProductStatus,
    Tenant,
    TenantOrderMap,
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from protocols.base import ProtocolError, ProtocolInsufficientFundsError
from protocols.factory import ProtocolFactory
from services.auto_failover_service import AutoFailoverService
from services.balance_service import BalanceService, InsufficientBalanceError
from services.catalog_routing_service import CatalogRoutingService
from services.inventory_service import InventoryError, InventoryService
from services.product_service import ProductService
from services.tenant_service import TenantService

logger = logging.getLogger(__name__)


class TenantOrderError(Exception):
    pass


@dataclass
class TenantPurchaseResult:
    order: UnifiedOrder
    merchant_total: Decimal
    base_total: Decimal
    fee: Decimal
    delivery_value: str | None = None


async def _get_product(session, product_id: int) -> Product:
    result = await session.execute(
        select(Product)
        .options(selectinload(Product.api_provider), selectinload(Product.sub_category))
        .where(Product.id == product_id)
    )
    product = result.scalar_one_or_none()
    if product is None or product.status != ProductStatus.ACTIVE:
        raise TenantOrderError("المنتج غير موجود أو غير متاح.")
    return product


def _category_id_of(product: Product) -> int | None:
    try:
        sub = product.sub_category
        if sub is not None:
            parent = getattr(sub, "parent", None)
            if parent is not None and getattr(parent, "category", None) is not None:
                return parent.category.id
            cat = getattr(sub, "category", None)
            if cat is not None:
                return cat.id
    except Exception:
        pass
    return None


class TenantOrderService:
    @staticmethod
    async def quote(
        session, tenant: Tenant, product_id: int, quantity: int = 1
    ) -> dict:
        """سعر البيع للزبون الفرعي قبل التأكيد (لا يخصم شيئاً)."""
        product = await _get_product(session, product_id)
        if not await TenantOrderService.is_visible(session, tenant, product):
            raise TenantOrderError("هذا المنتج غير متاح في هذا المتجر.")
        base = ProductService.calculate_order_total(product, quantity)
        merchant_total = await TenantService.merchant_price(
            session, tenant, base, _category_id_of(product)
        )
        return {
            "product": product,
            "base_total": base,
            "merchant_total": merchant_total,
            "fee": TenantService.platform_fee(base, tenant),
        }

    @staticmethod
    async def is_visible(session, tenant: Tenant, product: Product) -> bool:
        """هل المنتج ظاهر في كتالوج هذا المستأجر؟"""
        if tenant.catalog_mode == "full":
            return True
        from database.models import TenantCatalogSelection

        # انتقائي: قسم أو منتج محدد
        sub = getattr(product, "sub_category", None)
        checks: list[tuple[str, int]] = [("product", product.id)]
        if sub is not None:
            checks.append(("subcategory", sub.id))
            cat_id = _category_id_of(product)
            if cat_id is not None:
                checks.append(("category", cat_id))
        for item_type, item_id in checks:
            result = await session.execute(
                select(TenantCatalogSelection).where(
                    TenantCatalogSelection.tenant_id == tenant.id,
                    TenantCatalogSelection.item_type == item_type,
                    TenantCatalogSelection.item_id == item_id,
                )
            )
            if result.scalar_one_or_none() is not None:
                return True
        return False

    @staticmethod
    async def tenant_categories(session, tenant: Tenant) -> list:
        """أقسام الكتالوج المرئية لهذا المستأجر (للبناء)."""
        from database.models import Category, CategoryType, TenantCatalogSelection

        result = await session.execute(
            select(Category).order_by(Category.sort_order, Category.id)
        )
        categories = list(result.scalars().all())
        # المرحلة 1: منتجات المتجر الموحدة فقط. بيع الأرقام في الفروع
        # (البنية جاهزة: tenant_id + عزل المراقبة) مرحلة ثانية.
        categories = [c for c in categories if c.type != CategoryType.NUMBERS]
        if tenant.catalog_mode == "full":
            return categories
        sel = (
            await session.execute(
                select(TenantCatalogSelection.item_id).where(
                    TenantCatalogSelection.tenant_id == tenant.id,
                    TenantCatalogSelection.item_type == "category",
                )
            )
        ).scalars().all()
        picked = set(sel)
        if not picked:
            return []
        return [c for c in categories if c.id in picked]

    @staticmethod
    async def purchase(
        session,
        tenant: Tenant,
        sub_user: User,
        product_id: int,
        target: str = "",
        quantity: int = 1,
    ) -> TenantPurchaseResult:
        if not TenantService.is_usable(tenant):
            raise TenantOrderError("هذا المتجر موقوف حالياً — تواصل مع إدارته.")
        product = await _get_product(session, product_id)
        if not await TenantOrderService.is_visible(session, tenant, product):
            raise TenantOrderError("هذا المنتج غير متاح في هذا المتجر.")
        fulfillment = getattr(
            product.fulfillment_type, "value", product.fulfillment_type
        )
        base = ProductService.calculate_order_total(product, quantity)
        if base <= 0:
            raise TenantOrderError("سعر المنتج غير صالح.")
        merchant_total = await TenantService.merchant_price(
            session, tenant, base, _category_id_of(product)
        )
        fee = TenantService.platform_fee(base, tenant)
        wallet_cost = base + fee
        earned = merchant_total - base

        # فحوص مسبقة لرسائل واضحة قبل أي خصم
        if (sub_user.balance or Decimal("0")) < merchant_total:
            raise TenantOrderError(
                f"رصيدك غير كافٍ — تحتاج {merchant_total}$."
            )
        wallet = await TenantService.wallet(session, tenant.id)
        if (wallet.balance or Decimal("0")) < wallet_cost:
            raise TenantOrderError(
                "المتجر لا يستطيع تنفيذ طلبك حالياً — حاول لاحقاً."
            )

        if fulfillment == ProductFulfillmentType.INVENTORY.value:
            if target:
                raise TenantOrderError("منتج المخزون لا يحتاج هدفاً.")
            # المحفظة أولاً (استردادها دائماً ممكن)، ثم المخزون
            await TenantService.charge(session, tenant.id, wallet_cost, earned)
            try:
                order, delivery, _meta = await InventoryService.purchase(
                    session,
                    user_id=sub_user.id,
                    product_id=product.id,
                    price_usd=merchant_total,
                    quantity=quantity,
                    tenant_id=tenant.id,
                )
            except (InventoryError, InsufficientBalanceError) as exc:
                await TenantService.fund(
                    session, tenant.id, wallet_cost, f"استرداد مخزون فاشل #{product.id}"
                )
                raise TenantOrderError(str(exc)) from exc
            session.add(
                TenantOrderMap(
                    tenant_id=tenant.id,
                    sub_order_type="unified",
                    sub_order_id=order.id,
                    main_order_id=order.id,
                    base_price_usd=base,
                    fee_usd=fee,
                )
            )
            await session.commit()
            return TenantPurchaseResult(order, merchant_total, base, fee, delivery)

        if fulfillment != ProductFulfillmentType.API.value:
            raise TenantOrderError("المنتج غير قابل للشراء التلقائي.")
        if (getattr(product, "requires_link", False) or getattr(product, "requires_player_id", False)) and not target:
            raise TenantOrderError("هذا المنتج يحتاج رابطاً أو معرفاً لإتمام الطلب.")
        if not product.api_provider_id or not product.provider_service_id:
            raise TenantOrderError("مزود المنتج غير مضبوط.")

        # 1) المحفظة أولاً
        await TenantService.charge(session, tenant.id, wallet_cost, earned)
        # 2) الزبون — عند الفشل تُسترد المحفظة فوراً
        try:
            await BalanceService.deduct_balance(
                session,
                sub_user.id,
                merchant_total,
                TransactionType.PURCHASE,
                description=f"شراء {product.name_ar}",
                related_table="tenant_orders",
                related_id=tenant.id,
                is_purchase=True,
                tenant_id=tenant.id,
            )
        except InsufficientBalanceError as exc:
            await TenantService.fund(
                session, tenant.id, wallet_cost, "استرداد رصيد زبون غير كافٍ"
            )
            raise TenantOrderError("رصيدك غير كافٍ لإتمام الشراء.") from exc

        # 3) التنفيذ عبر المزودين
        routes = await CatalogRoutingService.routes_for(session, product)
        if not routes:
            await TenantOrderService._refund_all(
                session, tenant, sub_user, merchant_total, wallet_cost, "لا مزود متاح"
            )
            raise TenantOrderError("مزود المنتج غير متاح حالياً.")
        external = None
        used_route = None
        for route in routes:
            provider = await session.get(ApiProvider, route.api_provider_id)
            if provider is None or not provider.is_active:
                continue
            try:
                protocol = ProtocolFactory.create_from_provider(provider)
                external = await protocol.place_order(
                    service_id=route.provider_service_id,
                    target=target,
                    quantity=quantity,
                )
                used_route = route
                break
            except ProtocolInsufficientFundsError:
                logger.warning("رصيد المزود %s غير كافٍ (مستأجر %s)", route.api_provider_id, tenant.id)
                continue
            except ProtocolError as exc:
                logger.warning("فشل المزود %s للمستأجر %s: %s", route.api_provider_id, tenant.id, exc)
                try:
                    await AutoFailoverService.record_failure(
                        session, product.id, route.api_provider_id,
                        is_backup_route=not route.is_primary,
                    )
                except Exception:
                    pass
                continue

        if external is None or used_route is None:
            await TenantOrderService._refund_all(
                session, tenant, sub_user, merchant_total, wallet_cost, "فشل المزودين"
            )
            raise TenantOrderError("فشل إرسال الطلب للمزود وتم استرجاع المبلغ.")

        instant = str(getattr(external, "status", "")).lower() == "completed"
        order = UnifiedOrder(
            user_id=sub_user.id,
            tenant_id=tenant.id,
            product_id=product.id,
            api_provider_id=used_route.api_provider_id,
            external_order_id=external.external_order_id,
            target=target,
            quantity=quantity,
            price_usd=merchant_total,
            cost_price_usd=base,
            status=UnifiedOrderStatus.COMPLETED if instant else UnifiedOrderStatus.PROCESSING,
            status_message="مكتمل - توصيل فوري" if instant else "تم إرسال الطلب للمزود",
            result_data=json.dumps(external.raw, ensure_ascii=False) if instant else None,
            completed_at=datetime.utcnow() if instant else None,
        )
        session.add(order)
        await session.flush()
        delivery_value = None
        if instant:
            try:
                from services.digital_delivery import format_delivery_text

                delivery_value = format_delivery_text(external.raw)
            except Exception:
                delivery_value = None
        session.add(
            TenantOrderMap(
                tenant_id=tenant.id,
                sub_order_type="unified",
                sub_order_id=order.id,
                main_order_id=order.id,
                base_price_usd=base,
                fee_usd=fee,
            )
        )
        await session.commit()
        await session.refresh(order)
        return TenantPurchaseResult(order, merchant_total, base, fee, delivery_value)

    @staticmethod
    async def _refund_all(session, tenant, sub_user, merchant_total, wallet_cost, reason: str):
        try:
            await BalanceService.add_balance(
                session,
                sub_user.id,
                merchant_total,
                TransactionType.REFUND,
                description=f"استرجاع طلب فرعي فاشل ({reason})",
                payment_reference=f"tenant_refund:{tenant.id}:{sub_user.id}:{datetime.utcnow().timestamp()}",
                tenant_id=tenant.id,
            )
        except Exception:
            logger.exception("فشل استرجاع الزبون الفرعي %s", sub_user.id)
        try:
            await TenantService.fund(session, tenant.id, wallet_cost, f"استرداد ({reason})")
        except Exception:
            logger.exception("فشل استرداد محفظة المستأجر %s", tenant.id)

    @staticmethod
    async def refund_order(session, tenant: Tenant, sub_order_id: int, reason: str) -> bool:
        """استرجاع طلب فرعي: للزبون سعره + للمحفظة الأساسي والعمولة."""
        order = await session.get(UnifiedOrder, sub_order_id)
        if order is None or (order.tenant_id or 0) != tenant.id:
            return False
        if order.status == UnifiedOrderStatus.REFUNDED:
            return False
        map_result = await session.execute(
            select(TenantOrderMap).where(
                TenantOrderMap.tenant_id == tenant.id,
                TenantOrderMap.sub_order_type == "unified",
                TenantOrderMap.sub_order_id == order.id,
            )
        )
        mapping = map_result.scalar_one_or_none()
        base = mapping.base_price_usd if mapping else order.cost_price_usd
        fee = mapping.fee_usd if mapping else Decimal("0")
        order.status = UnifiedOrderStatus.REFUNDED
        await session.flush()
        await BalanceService.add_balance(
            session,
            order.user_id,
            order.price_usd,
            TransactionType.REFUND,
            description=f"استرجاع طلب #{order.id} ({reason})",
            related_table="unified_orders",
            related_id=order.id,
            tenant_id=tenant.id,
        )
        await TenantService.fund(
            session, tenant.id, base + fee, f"استرداد طلب فرعي #{order.id}"
        )
        order.status = UnifiedOrderStatus.REFUNDED
        await session.commit()
        return True
