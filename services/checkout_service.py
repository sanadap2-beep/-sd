"""Checkout core shared by HTTP clients and future storefronts."""

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
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
)
from protocols.base import ProtocolError, ProtocolInsufficientFundsError
from protocols.factory import ProtocolFactory
from services.balance_service import BalanceService, InsufficientBalanceError
from services.catalog_routing_service import CatalogRoutingService
from services.cashback_service import CashbackService
from services.feature_service import FeatureService
from services.dynamic_service import DynamicService
from services.gamification_service import GamificationService
from services.input_validation_service import InputValidationError, InputValidationService
from services.inventory_service import InventoryError, InventoryService
from services.loyalty_service import LoyaltyService
from services.product_service import ProductService
from services.promotion_service import PromotionService
from services.tiered_pricing_service import TieredPricingService


logger = logging.getLogger(__name__)


class CheckoutError(Exception):
    pass


@dataclass
class CheckoutResult:
    order: UnifiedOrder
    discount: Decimal = Decimal("0")
    delivery_value: str | None = None


class CheckoutService:
    @staticmethod
    def calculate_total(product: Product, quantity: int) -> Decimal:
        try:
            quantity = InputValidationService.integer(quantity, minimum=1, maximum=1_000_000)
        except InputValidationError as exc:
            raise CheckoutError(str(exc)) from exc
        if not product.requires_quantity and quantity != 1:
            raise CheckoutError("هذا المنتج لا يقبل كمية متعددة.")
        if product.requires_quantity:
            if quantity < product.min_quantity or quantity > product.max_quantity:
                raise CheckoutError(
                    f"الكمية يجب أن تكون بين {product.min_quantity} و{product.max_quantity}."
                )
        total = ProductService.calculate_order_total(product, quantity)
        return total.quantize(Decimal("0.0001"))

    @staticmethod
    async def _get_product(session, product_id: int) -> Product:
        result = await session.execute(
            select(Product)
            .options(selectinload(Product.api_provider), selectinload(Product.sub_category))
            .where(Product.id == product_id)
        )
        product = result.scalar_one_or_none()
        if product is None or product.status != ProductStatus.ACTIVE:
            raise CheckoutError("المنتج غير موجود أو غير متاح.")
        return product

    @staticmethod
    async def purchase(
        session,
        user_id: int,
        product_id: int,
        target: str = "",
        quantity: int = 1,
    ) -> CheckoutResult:
        product = await CheckoutService._get_product(session, product_id)
        fulfillment = getattr(product.fulfillment_type, "value", product.fulfillment_type)
        total = CheckoutService.calculate_total(product, quantity)
        promotion, promotion_discount = await PromotionService.get_best_promotion(
            session, product.id, total
        )
        tier_discount, _tier_label = await TieredPricingService.discount_for(
            session, user_id, product, total, quantity
        )
        discount = max(promotion_discount, tier_discount)
        if discount == tier_discount and tier_discount > promotion_discount:
            promotion = None
        price = total - discount

        if fulfillment == ProductFulfillmentType.INVENTORY.value:
            if target:
                raise CheckoutError("منتج المخزون لا يحتاج هدفاً.")
            try:
                order, delivery, _metadata = await InventoryService.purchase(
                    session,
                    user_id=user_id,
                    product_id=product.id,
                    price_usd=price,
                    quantity=quantity,
                    promotion_id=promotion.id if promotion else None,
                )
            except (InventoryError, InsufficientBalanceError) as exc:
                raise CheckoutError(str(exc)) from exc
            if promotion:
                await PromotionService.mark_used(session, promotion.id)
            await DynamicService.increment_product_sold(session, product.id, quantity)
            await CashbackService.apply_cashback(
                session, user_id, order.id, "unified_orders", price
            )
            await LoyaltyService.award_purchase_points(
                session, user_id, "unified_orders", order.id, price
            )
            await GamificationService.progress_event(session, user_id, "purchase")
            return CheckoutResult(order, discount, delivery)

        if fulfillment != ProductFulfillmentType.API.value:
            raise CheckoutError("المنتج غير قابل للشراء التلقائي.")
        if (product.requires_link or product.requires_player_id) and not target:
            raise CheckoutError("هذا المنتج يحتاج رابطاً أو معرفاً لإتمام الطلب.")
        if not product.api_provider_id or not product.provider_service_id:
            raise CheckoutError("مزود المنتج غير مضبوط.")

        # ── مسارات التنفيذ: الأساسي ثم الاحتياطي ──
        # كان المزود الواحد يعني أن تعطّله يُغلق المنتج كلياً. الآن يُجرَّب
        # كل مزود مسجّل لهذا المنتج بدوره.
        routes = await CatalogRoutingService.routes_for(session, product)
        if not routes:
            raise CheckoutError("مزود المنتج غير متاح حالياً.")

        try:
            await BalanceService.deduct_balance(
                session,
                user_id,
                price,
                TransactionType.PURCHASE,
                description=f"شراء {product.name_ar}",
                is_purchase=True,
            )
        except InsufficientBalanceError as exc:
            raise CheckoutError(str(exc)) from exc

        # ── تجرَّب المسارات حتى ينجح أحدها ──
        external = None
        used_route = None
        errors: list[str] = []
        for route in routes:
            provider = await session.get(ApiProvider, route.api_provider_id)
            if provider is None or not provider.is_active:
                errors.append(f"المزود {route.api_provider_id} غير نشط")
                continue
            try:
                protocol = ProtocolFactory.create_from_provider(provider)
                external = await protocol.place_order(
                    service_id=route.provider_service_id,
                    target=target,
                    quantity=quantity,
                )
                used_route = route
                if not route.is_primary:
                    await FeatureService.track(
                        "catalog_failover", "failover_used", user_id=user_id,
                        value=f"product:{product.id}:provider:{route.api_provider_id}",
                    )
                    logger.info(
                        "المنتج %s نُفِّذ عبر مزود احتياطي %s بعد تعذّر الأساسي.",
                        product.id, route.api_provider_id,
                    )
                break
            except ProtocolError as exc:
                errors.append(f"المزود {route.api_provider_id}: {exc}")
                logger.warning(
                    "فشل تنفيذ المنتج %s لدى المزود %s: %s",
                    product.id, route.api_provider_id, exc,
                )
                continue

        if external is None or used_route is None:
            await BalanceService.add_balance(
                session,
                user_id,
                price,
                TransactionType.REFUND,
                description="استرجاع - فشل كل مزودي المنتج",
            )
            raise CheckoutError("فشل إرسال الطلب للمزود وتم استرجاع الرصيد.")

        # منتجات رقمية لحظية (اشتراكات ggsoma…): المزود يسلّم فوراً في نفس
        # الاستجابة، فنكمل الطلب هنا مباشرة ونعرض التسليم للمشتري فوراً،
        # بدلاً من انتظار جولة المراقبة. غيرها يبقى قيد المعالجة كالمعتاد.
        from services.digital_delivery import format_delivery_text

        instant = str(getattr(external, "status", "")).lower() == "completed"
        order = UnifiedOrder(
            user_id=user_id,
            product_id=product.id,
            api_provider_id=used_route.api_provider_id,
            promotion_id=promotion.id if promotion else None,
            external_order_id=external.external_order_id,
            target=target,
            quantity=quantity,
            price_usd=price,
            cost_price_usd=product.cost_price_usd,
            status=(
                UnifiedOrderStatus.COMPLETED
                if instant
                else UnifiedOrderStatus.PROCESSING
            ),
            status_message="مكتمل - توصيل فوري" if instant else "تم إرسال الطلب للمزود",
            result_data=json.dumps(external.raw, ensure_ascii=False) if instant else None,
            completed_at=datetime.utcnow() if instant else None,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        if promotion:
            await PromotionService.mark_used(session, promotion.id)
        delivery_value = format_delivery_text(external.raw) if instant else None
        return CheckoutResult(order, discount, delivery_value)
