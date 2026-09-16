"""Shopping cart storage and validation."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from database.models import CartItem, Product, ProductStatus


class CartError(Exception):
    pass


class CartService:
    @staticmethod
    async def get_items(session, user_id: int) -> list[CartItem]:
        result = await session.execute(
            select(CartItem)
            .options(selectinload(CartItem.product))
            .where(CartItem.user_id == user_id)
            .order_by(CartItem.created_at)
        )
        return list(result.scalars().all())

    @staticmethod
    async def add(
        session,
        user_id: int,
        product_id: int,
        target: str = "",
        quantity: int = 1,
    ) -> CartItem:
        product = await session.get(Product, product_id)
        if product is None or product.status != ProductStatus.ACTIVE:
            raise CartError("المنتج غير متاح.")
        if quantity < 1:
            raise CartError("الكمية غير صالحة.")
        if (
            product.requires_quantity
            and not product.min_quantity <= quantity <= product.max_quantity
        ):
            raise CartError("الكمية خارج الحدود المسموحة.")
        if not product.requires_quantity and quantity != 1:
            raise CartError("هذا المنتج لا يقبل كمية متعددة.")
        if product.requires_link or product.requires_player_id:
            if not target or len(target) > 500:
                raise CartError("هذا المنتج يحتاج رابطاً أو معرفاً صالحاً.")
        else:
            target = ""

        result = await session.execute(
            select(CartItem).where(
                CartItem.user_id == user_id,
                CartItem.product_id == product_id,
            )
        )
        item = result.scalar_one_or_none()
        if item:
            item.quantity = quantity
            item.target = target or None
        else:
            item = CartItem(
                user_id=user_id,
                product_id=product_id,
                target=target or None,
                quantity=quantity,
            )
            session.add(item)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise CartError("تعذر إضافة المنتج للسلة.") from None
        await session.refresh(item)
        return item

    @staticmethod
    async def remove(session, user_id: int, product_id: int) -> bool:
        result = await session.execute(
            select(CartItem).where(
                CartItem.user_id == user_id,
                CartItem.product_id == product_id,
            )
        )
        item = result.scalar_one_or_none()
        if item is None:
            return False
        await session.delete(item)
        await session.commit()
        return True

    @staticmethod
    async def clear(session, user_id: int) -> None:
        items = await CartService.get_items(session, user_id)
        for item in items:
            await session.delete(item)
        await session.commit()

    @staticmethod
    def item_total(item: CartItem) -> Decimal:
        from services.checkout_service import CheckoutService

        return CheckoutService.calculate_total(item.product, item.quantity)

    @staticmethod
    async def checkout(
        session, user_id: int, coupon_code: str | None = None
    ) -> dict:
        """Process cart items and keep failed items for retry.

        External providers cannot share a database transaction, so partial
        completion is returned explicitly rather than hidden from the user.
        """
        from services.checkout_service import CheckoutError, CheckoutService

        items = await CartService.get_items(session, user_id)
        results = {"completed": [], "failed": [], "total_saved_usd": Decimal("0")}
        for item in items:
            try:
                checkout = await CheckoutService.purchase(
                    session,
                    user_id,
                    item.product_id,
                    item.target or "",
                    item.quantity,
                    coupon_code=coupon_code,
                )
                results["completed"].append((item, checkout))
                results["total_saved_usd"] += checkout.discount
                await CartService.remove(session, user_id, item.product_id)
            except CheckoutError as exc:
                results["failed"].append((item, str(exc)))
        return results

    @staticmethod
    def total(items: list[CartItem]) -> Decimal:
        return sum(
            (CartService.item_total(item) for item in items),
            Decimal("0"),
        ).quantize(Decimal("0.0001"))
