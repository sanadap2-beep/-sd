"""Stable API response models."""

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class ProductOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    price_usd: Decimal
    display_type: str
    min_quantity: int
    max_quantity: int
    requires_link: bool
    requires_quantity: bool
    requires_player_id: bool
    fulfillment_type: str
    rating: Decimal | None = None
    reviews_count: int = 0
    promotion: dict[str, Any] | None = None


class SubCategoryOut(BaseModel):
    id: int
    name: str
    emoji: str
    products: list[ProductOut]


class CategoryOut(BaseModel):
    id: int
    name: str
    emoji: str
    type: str
    sub_categories: list[SubCategoryOut]


class CatalogOut(BaseModel):
    categories: list[CategoryOut]
    generated_at: str


class MeOut(BaseModel):
    telegram_id: int
    username: str | None
    full_name: str | None
    balance_usd: Decimal
    loyalty_points: int
    loyalty_streak: int
    loyalty_tier: str


class OrderOut(BaseModel):
    order_type: str
    id: int
    product_id: int | None = None
    product_name: str | None = None
    status: str
    price_usd: Decimal
    quantity: int
    target: str | None = None
    created_at: str
    completed_at: str | None = None
    status_message: str | None = None


class GiftRedeemIn(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class WatchIn(BaseModel):
    product_id: int


class CheckoutIn(BaseModel):
    product_id: int
    target: str = ""
    quantity: int = Field(default=1, ge=1, le=1_000_000)
    coupon_code: str | None = None


class CheckoutOut(BaseModel):
    order_id: int
    status: str
    price_usd: Decimal
    discount_usd: Decimal
    delivery: str | None = None


class ResellerOrderIn(BaseModel):
    product_id: int
    target: str = ""
    quantity: int = Field(default=1, ge=1, le=1_000_000)


class CartItemIn(BaseModel):
    product_id: int
    target: str = ""
    quantity: int = Field(default=1, ge=1, le=1_000_000)


class CartItemOut(BaseModel):
    product_id: int
    product_name: str
    target: str | None
    quantity: int
    unit_price_usd: Decimal
    total_price_usd: Decimal


class ResellerCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    user_id: int
    label: str | None = Field(default=None, max_length=128)


class ErrorOut(BaseModel):
    detail: str
