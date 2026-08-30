"""Wholesale reseller API authenticated by dedicated hashed keys."""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from api.deps import get_session
from api.schemas import ResellerOrderIn
from database.models import Product, ProductStatus, UnifiedOrder
from services.checkout_service import CheckoutError, CheckoutService
from services.reseller_api_service import ResellerAPIService, ResellerAuthError


async def get_reseller_account(
    x_reseller_key: str | None = Header(default=None),
    session=Depends(get_session),
):
    try:
        return await ResellerAPIService.authenticate(session, x_reseller_key or "")
    except ResellerAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


router = APIRouter(prefix="/api/v1/reseller", tags=["reseller"])


@router.get("/catalog")
async def reseller_catalog(
    reseller=Depends(get_reseller_account),
    session=Depends(get_session),
):
    result = await session.execute(
        select(Product)
        .options(selectinload(Product.sub_category))
        .where(Product.status == ProductStatus.ACTIVE)
        .order_by(Product.sort_order, Product.id)
    )
    return [
        {
            "id": product.id,
            "name": product.name_ar,
            "price_usd": product.price_usd,
            "reseller_markup_percent": reseller.markup_percent,
            "min_quantity": product.min_quantity,
            "max_quantity": product.max_quantity,
            "requires_link": product.requires_link,
            "requires_player_id": product.requires_player_id,
            "requires_quantity": product.requires_quantity,
        }
        for product in result.scalars().all()
        if product.fulfillment_type.value == "api"
    ]


@router.post("/orders")
async def reseller_order(
    payload: ResellerOrderIn,
    reseller=Depends(get_reseller_account),
    session=Depends(get_session),
):
    try:
        result = await CheckoutService.purchase(
            session,
            reseller.user_id,
            payload.product_id,
            payload.target.strip(),
            payload.quantity,
        )
    except CheckoutError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "order_id": result.order.id,
        "status": result.order.status.value,
        "price_usd": result.order.price_usd,
        "discount_usd": result.discount,
        "external_order_id": result.order.external_order_id,
    }


@router.get("/orders")
async def reseller_orders(
    reseller=Depends(get_reseller_account),
    session=Depends(get_session),
):
    result = await session.execute(
        select(UnifiedOrder)
        .options(selectinload(UnifiedOrder.product))
        .where(UnifiedOrder.user_id == reseller.user_id)
        .order_by(desc(UnifiedOrder.created_at))
        .limit(100)
    )
    return [
        {
            "order_id": order.id,
            "product": order.product.name_ar if order.product else None,
            "status": order.status.value,
            "price_usd": order.price_usd,
            "external_order_id": order.external_order_id,
            "created_at": order.created_at.isoformat(),
        }
        for order in result.scalars().all()
    ]
