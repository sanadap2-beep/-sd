"""تنبيهات السعر وعودة المخزون للمستخدمين."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import (
    Product,
    ProductStatus,
    ProductFulfillmentType,
    ProductWatch,
)
from database.engine import async_session_maker
from services.inventory_service import InventoryService
from services.notification_service import NotificationService


class WatchService:
    @staticmethod
    async def toggle(session, user_id: int, product_id: int) -> bool:
        """بدّل الاشتراك وأرجع الحالة الجديدة."""
        product = await session.get(Product, product_id)
        if product is None or product.status != ProductStatus.ACTIVE:
            raise ValueError("المنتج غير موجود أو غير فعال.")
        result = await session.execute(
            select(ProductWatch).where(
                ProductWatch.user_id == user_id,
                ProductWatch.product_id == product_id,
            )
        )
        watch = result.scalar_one_or_none()
        if watch:
            await session.delete(watch)
            await session.commit()
            return False
        stock = 0
        if product.fulfillment_type == ProductFulfillmentType.INVENTORY:
            stock = await InventoryService.available_count(session, product_id)
        session.add(
            ProductWatch(
                user_id=user_id,
                product_id=product_id,
                last_seen_price=product.price_usd,
                last_seen_stock=stock,
                is_active=True,
            )
        )
        await session.commit()
        return True

    @staticmethod
    async def list_user_watches(session, user_id: int) -> list[ProductWatch]:
        result = await session.execute(
            select(ProductWatch)
            .options(selectinload(ProductWatch.product))
            .where(ProductWatch.user_id == user_id, ProductWatch.is_active.is_(True))
            .order_by(ProductWatch.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def check_all(bot) -> int:
        """افحص التنبيهات وأرسل فقط التغييرات الجديدة."""
        sent = 0
        async with async_session_maker() as session:
            result = await session.execute(
                select(ProductWatch)
                .options(
                    selectinload(ProductWatch.product),
                    selectinload(ProductWatch.user),
                )
                .where(ProductWatch.is_active.is_(True))
            )
            watches = list(result.scalars().all())
            for watch in watches:
                product = watch.product
                user = watch.user
                if not product or not user or product.status != ProductStatus.ACTIVE:
                    continue
                stock = watch.last_seen_stock
                if product.fulfillment_type == ProductFulfillmentType.INVENTORY:
                    stock = await InventoryService.available_count(session, product.id)
                price_changed = watch.last_seen_price != product.price_usd
                restocked = watch.last_seen_stock == 0 and stock > 0
                if price_changed or restocked:
                    parts = [f"🔔 <b>تحديث على {product.name_ar}</b>"]
                    if price_changed:
                        direction = "انخفض" if product.price_usd < watch.last_seen_price else "تغير"
                        parts.append(
                            f"💰 السعر {direction}: {watch.last_seen_price}$ → {product.price_usd}$"
                        )
                    if restocked:
                        parts.append("📦 عاد المنتج إلى المخزون.")
                    parts.append("افتح الكتالوج الآن للاستفادة.")
                    await NotificationService(bot).notify_user(user.telegram_id, "\n".join(parts))
                    watch.last_notified_at = datetime.utcnow()
                    sent += 1
                watch.last_seen_price = Decimal(str(product.price_usd))
                watch.last_seen_stock = stock
            await session.commit()
        return sent
