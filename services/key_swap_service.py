"""
ضمان المفتاح مع تبديل فوري.

المشكلة: لو طلع الكود المباع مستخدماً أو تالفاً، على المستخدم فتح
تذكرة وانتظار الأدمن. وهذا هو الخوف رقم 1 من شراء الأكواد أونلاين.

الحل: عند الإبلاغ (أو الفشل المكتشف آلياً) يُتلف المفتاح الفاسد
(`InventoryItemStatus.VOID` موجود أصلاً) ويُسلَّم بديل من المخزون
فوراً بلا تدخل بشري، بعدد تبديلات محدود يضبطه الأدمن.

لماذا لا يُستغل: كل تبديل يسجَّل في جدول خاص بمفتاح idempotent، فطلب
التبديل المكرر لا يستهلك مخزوناً إضافياً.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import func, select

from database.models import (
    DigitalInventoryItem,
    InventoryItemStatus,
    Product,
    ProductFulfillmentType,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from services.feature_service import FeatureService
from services.inventory_service import InventoryService
from services.notification_service import NotificationService

logger = logging.getLogger(__name__)


class KeySwapError(Exception):
    pass


class KeySwapService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("key_swap_guarantee")

    @staticmethod
    async def max_swaps() -> int:
        return await FeatureService.config_int("key_swap_guarantee", "max_swaps_per_order", 2)

    @staticmethod
    async def swap(
        session,
        order_id: int,
        user_id: int,
        reason: str,
        bot=None,
    ) -> str:
        """
        يستبدل مفتاح طلب فاسداً بمفتاح سليم من المخزون.
        يرجع المفتاح الجديد أو يرمي KeySwapError.
        """
        if not await KeySwapService.enabled():
            raise KeySwapError("ضمان المفتاح موقوف حالياً.")

        order = await session.get(UnifiedOrder, order_id)
        if order is None or order.user_id != user_id:
            raise KeySwapError("الطلب غير موجود.")
        if order.status != UnifiedOrderStatus.COMPLETED:
            raise KeySwapError("لا يمكن استبدال مفتاح طلب غير مكتمل.")

        product = await session.get(Product, order.product_id)
        if product is None:
            raise KeySwapError("المنتج غير موجود.")
        fulfillment = getattr(
            product.fulfillment_type, "value", product.fulfillment_type
        )
        if fulfillment != ProductFulfillmentType.INVENTORY.value:
            raise KeySwapError("هذا المنتج ليس من مخزون الأكواد.")

        swaps = int(order.key_swaps or 0)
        if swaps >= await KeySwapService.max_swaps():
            raise KeySwapError(
                "استنفدت عدد التبديلات المسموح. تواصل مع الدعم للمساعدة."
            )

        available = await InventoryService.available_count(session, product.id)
        if available <= 0:
            raise KeySwapError(
                "لا يوجد بديل في المخزون حالياً. سيفتح الدعم تذكرة لك تلقائياً."
            )

        # ── إتلاف المفتاح القديم ──
        # العنصر المباع مربوط مباشرة بـ unified_order_id، فلا حاجة لفك
        # تشفير ومقارنة نصوص؛ result_data أصلاً JSON لا كود مشفر.
        old_item_result = await session.execute(
            select(DigitalInventoryItem).where(
                DigitalInventoryItem.unified_order_id == order.id,
            )
        )
        old_item = old_item_result.scalars().first()
        if old_item is not None:
            old_item.status = InventoryItemStatus.VOID
            old_item.unified_order_id = None

        # ── تسليم بديل بسعر صفر (الضمان على حساب المنصة) ──
        try:
            _swap_order, new_code, _meta, _replayed = await InventoryService.purchase(
                session,
                user_id=user_id,
                product_id=product.id,
                price_usd=Decimal("0"),
                quantity=1,
                promotion_id=None,
            )
        except Exception as exc:  # noqa: BLE001
            # نعيد القديم كما كان حتى لا يخسر المستخدم مفتاحه عند الفشل
            if old_item is not None:
                old_item.status = InventoryItemStatus.SOLD
                old_item.unified_order_id = order.id
                await session.commit()
            raise KeySwapError("تعذّر سحب بديل من المخزون.") from exc

        order.key_swaps = swaps + 1
        order.status_message = f"تم تبديل المفتاح ({swaps + 1})"
        await session.commit()

        await FeatureService.track(
            "key_swap_guarantee", "swapped", user_id=user_id, value=str(order.id)
        )

        if bot is not None:
            try:
                user = await session.get(User, user_id)
                await NotificationService(bot).notify_user(
                    user.telegram_id,
                    "🔑 <b>تم تبديل المفتاح</b>\n\n"
                    f"📦 {product.name_ar}\n"
                    f"🆕 الكود الجديد:\n<code>{new_code}</code>\n\n"
                    "المفتاح القديم أُتلف ولن يعمل لأحد.",
                )
            except Exception:  # noqa: BLE001
                pass

        return new_code

    @staticmethod
    async def stats(session) -> dict:
        total_swaps = (
            await session.execute(
                select(func.coalesce(func.sum(UnifiedOrder.key_swaps), 0))
            )
        ).scalar_one()
        orders_with_swap = (
            await session.execute(
                select(func.count(UnifiedOrder.id)).where(UnifiedOrder.key_swaps > 0)
            )
        ).scalar_one()
        return {
            "total_swaps": int(total_swaps or 0),
            "orders_with_swap": int(orders_with_swap or 0),
        }
