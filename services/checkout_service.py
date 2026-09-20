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
from services.agent_service import AgentService
from services.auto_failover_service import AutoFailoverService
from services.balance_service import BalanceService, InsufficientBalanceError
from services.campaign_service import CampaignCodeError, CampaignService
from services.catalog_routing_service import CatalogRoutingService
from services.cashback_service import CashbackService
from services.coupon_service import CouponError, CouponService
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
    async def _resolve_discount(
        session,
        user_id: int,
        product: Product,
        total: Decimal,
        quantity: int,
        coupon_code: str | None = None,
        allow_coupon: bool = True,
    ) -> tuple[Decimal, object | None, object | None, object | None]:
        """
        يحسب الخصم الأفضل: كوبون › كود حملة › خصم عروض › خصم طبقات.
        يُرجع (الخصم، الكائن النشط، كوبون/حملة، هل كود الخصم سيّد الخصم).
        الأولوية: كود الخصم (الأعلى تفضيلاً — كوبون ثم كود حملة إن كانت
        ميزة campaign_codes مفعلة) ثم عرض ثم طبقات — بنفس منطق games.py.
        """
        from services.promotion_service import PromotionService

        promotion, promotion_discount = await PromotionService.get_best_promotion(
            session, product.id, total
        )
        tier_discount, _tier_label = await TieredPricingService.discount_for(
            session, user_id, product, total, quantity
        )

        discount = Decimal("0")
        coupon = None
        campaign = None
        if coupon_code and allow_coupon:
            # مخزون لا يقبل كوبونات (انظر games.py) — الكود فقط للمنتجات API.
            try:
                coupon = await CouponService.validate_coupon(session, coupon_code, user_id, total)
                discount = CouponService.calculate_discount(coupon, total)
            except CouponError:
                coupon = None
                if await FeatureService.enabled("campaign_codes"):
                    try:
                        campaign = await CampaignService.validate(
                            session, coupon_code, user_id, total
                        )
                        discount = CampaignService.calculate_discount(campaign, total)
                    except CampaignCodeError:
                        discount = Decimal("0")
                        campaign = None

        if promotion_discount >= max(discount, tier_discount) and promotion_discount > 0:
            discount = promotion_discount
            coupon = None
            campaign = None
        elif tier_discount > discount:
            discount = tier_discount
            coupon = None
            campaign = None
            promotion = None
        elif discount > 0:
            promotion = None
        return discount, coupon, promotion, campaign

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
        coupon_code: str | None = None,
    ) -> CheckoutResult:
        product = await CheckoutService._get_product(session, product_id)
        fulfillment = getattr(product.fulfillment_type, "value", product.fulfillment_type)
        total = CheckoutService.calculate_total(product, quantity)
        discount, coupon, promotion, campaign = await CheckoutService._resolve_discount(
            session,
            user_id,
            product,
            total,
            quantity,
            coupon_code=coupon_code,
            allow_coupon=fulfillment != ProductFulfillmentType.INVENTORY.value,
        )
        price = total - discount
        # خصم الوكيل (إن كان مستخدمه وكلاً فعّلاً): على السعر بعد كل الخصومات
        price = await AgentService.apply_discount(session, user_id, price)

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

        # ── حارس رصيد المزود (الاشتراكات الرقمية) ──
        # قبل خصم رصيد المستخدم نفحص رصيد المزود المفضَّل. إن كان معلوماً
        # وأقل من سعر الطلب، لا نخصم ولا نرسل — يُترك الطلب بانتظار تنفيذ
        # الإدارة بدل فشلٍ من المزود يضطرنا للاسترجاع لاحقاً.
        for route in routes:
            candidate_provider = await session.get(ApiProvider, route.api_provider_id)
            if (
                candidate_provider
                and candidate_provider.is_active
                and _is_digital_subscription_provider(candidate_provider)
            ):
                balance = await _provider_balance_usd(candidate_provider)
                if balance is not None and balance < price:
                    order = UnifiedOrder(
                        user_id=user_id,
                        product_id=product.id,
                        api_provider_id=candidate_provider.id,
                        promotion_id=promotion.id if promotion else None,
                        target=target,
                        quantity=quantity,
                        price_usd=price,
                        cost_price_usd=product.cost_price_usd,
                        status=UnifiedOrderStatus.PENDING,
                        status_message="بانتظار تنفيذ الإدارة (رصيد المزود غير كافٍ)",
                    )
                    session.add(order)
                    await session.commit()
                    await session.refresh(order)
                    if promotion:
                        await PromotionService.mark_used(session, promotion.id)
                    await CashbackService.apply_cashback(
                        session, user_id, order.id, "unified_orders", price
                    )
                    await LoyaltyService.award_purchase_points(
                        session, user_id, "unified_orders", order.id, price
                    )
                    raise CheckoutError(
                        "⚠️ المزود المنفذ يحتاج شحناً مؤقتاً. تم تسجيل طلبك "
                        "الان بانتظار تنفيذ الإدارة — سنرسل لك فور جاهزيته."
                    )
            break

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
        if coupon is not None and discount > 0:
            try:
                await CouponService.apply_coupon(session, coupon, user_id, discount)
            except CouponError as exc:
                await BalanceService.add_balance(
                    session,
                    user_id,
                    price,
                    TransactionType.REFUND,
                    description="استرجاع - تعذر تطبيق الكوبون",
                )
                raise CheckoutError(str(exc)) from exc
        elif campaign is not None and discount > 0:
            try:
                await CampaignService.apply(session, campaign, user_id, discount)
            except CampaignCodeError as exc:
                await BalanceService.add_balance(
                    session,
                    user_id,
                    price,
                    TransactionType.REFUND,
                    description="استرجاع - تعذر تطبيق كود الحملة",
                )
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
            except ProtocolInsufficientFundsError as exc:
                errors.append(f"المزود {route.api_provider_id}: {exc}")
                logger.warning(
                    "رصيد المزود %s غير كافٍ للمنتج %s: %s",
                    route.api_provider_id, product.id, exc,
                )
                continue
            except ProtocolError as exc:
                errors.append(f"المزود {route.api_provider_id}: {exc}")
                logger.warning(
                    "فشل تنفيذ المنتج %s لدى المزود %s: %s",
                    product.id, route.api_provider_id, exc,
                )
                try:
                    await AutoFailoverService.record_failure(
                        session,
                        product.id,
                        route.api_provider_id,
                        is_backup_route=not route.is_primary,
                    )
                except Exception:
                    pass
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


# ─── أدوات مساعدة: حارس رصيد المزود للاشتراكات الرقمية ──────────────


def _is_digital_subscription_provider(provider: ApiProvider) -> bool:
    """هل مزود اشتراكات رقمية (تسليم لحظي)؟ ggsoma/partner_v1."""
    raw = getattr(provider, "custom_config", None)
    if not raw:
        return False
    try:
        cfg = json.loads(raw)
    except Exception:
        return False
    return isinstance(cfg, dict) and cfg.get("engine") in {"ggsoma", "partner_v1"}


async def _provider_balance_usd(provider: ApiProvider) -> Decimal | None:
    """
    رصيد المزود بالدولار، أو None إن لم يُعرف مسبقاً.
    الجهل لا يمنع البيع — نستعمل الرصيد المبلغ فقط عندما يكون موجوداً.
    """
    try:
        raw = getattr(provider, "rate_to_usd", None) or Decimal("1")
        if getattr(provider, "balance", None) is not None:
            return Decimal(str(provider.balance)) * Decimal(str(raw))
    except Exception:
        pass
    return None
