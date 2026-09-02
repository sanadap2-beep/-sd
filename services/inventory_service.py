"""تخزين وتسليم أكواد المنتجات الرقمية بشكل مشفّر.

هذا المسار مخصص للتراخيص والقسائم والاشتراكات التي يملكها المتجر قانونياً.
لا يُسمح باستخدامه لتخزين جلسات Telegram أو رموز دخول أو حسابات مسروقة.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select, update

from database.models import (
    DigitalInventoryItem,
    InventoryItemStatus,
    Product,
    ProductFulfillmentType,
    Transaction,
    TransactionType,
    UnifiedOrder,
    UnifiedOrderStatus,
    User,
)
from services.balance_service import BalanceService, InsufficientBalanceError
from services.encryption_service import EncryptionError, EncryptionService


class InventoryError(Exception):
    """خطأ قابل للعرض في إدارة المخزون."""


class InventoryService:
    @staticmethod
    async def available_count(session, product_id: int) -> int:
        result = await session.execute(
            select(DigitalInventoryItem.id).where(
                DigitalInventoryItem.product_id == product_id,
                DigitalInventoryItem.status == InventoryItemStatus.AVAILABLE,
            )
        )
        return len(result.scalars().all())

    @staticmethod
    def decrypt_value(encrypted_value: str) -> str:
        try:
            return EncryptionService.decrypt(encrypted_value)
        except EncryptionError:
            raise InventoryError("تعذر فك تشفير عنصر المخزون؛ تحقق من المفتاح.") from None

    @staticmethod
    async def add_item(
        session,
        product_id: int,
        value: str,
        metadata: dict | None = None,
    ) -> DigitalInventoryItem:
        product = await session.get(Product, product_id)
        if product is None:
            raise InventoryError("المنتج غير موجود.")
        if product.fulfillment_type != ProductFulfillmentType.INVENTORY:
            raise InventoryError("المنتج غير مضبوط كمنتج مخزون رقمي.")
        value = value.strip()
        if not value or len(value) > 1500:
            raise InventoryError("قيمة المخزون فارغة أو طويلة جداً.")

        try:
            encrypted = EncryptionService.encrypt(value)
        except EncryptionError as exc:
            raise InventoryError(str(exc)) from exc
        item = DigitalInventoryItem(
            product_id=product_id,
            encrypted_value=encrypted,
            metadata_json=(json.dumps(metadata, ensure_ascii=False) if metadata else None),
            status=InventoryItemStatus.AVAILABLE,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item

    @staticmethod
    async def purchase(
        session,
        user_id: int,
        product_id: int,
        price_usd,
        quantity: int = 1,
        promotion_id: int | None = None,
    ) -> tuple[UnifiedOrder, str, dict | None]:
        """يسلّم أول عنصر متاح ويخصم الرصيد في معاملة واحدة."""
        if quantity != 1:
            raise InventoryError("منتج المخزون يباع بعنصر واحد لكل طلب.")

        async with BalanceService._get_lock(user_id):
            user = await session.get(User, user_id)
            product = await session.get(Product, product_id)
            if user is None or product is None:
                raise InventoryError("المستخدم أو المنتج غير موجود.")
            if product.fulfillment_type != ProductFulfillmentType.INVENTORY:
                raise InventoryError("هذا المنتج ليس من نوع المخزون الرقمي.")
            if user.balance < price_usd:
                raise InsufficientBalanceError("رصيدك غير كافٍ.")

            item = None
            for _ in range(3):
                candidate = await session.execute(
                    select(DigitalInventoryItem)
                    .where(
                        DigitalInventoryItem.product_id == product_id,
                        DigitalInventoryItem.status == InventoryItemStatus.AVAILABLE,
                    )
                    .order_by(DigitalInventoryItem.id)
                    .limit(1)
                )
                candidate = candidate.scalar_one_or_none()
                if candidate is None:
                    raise InventoryError("لا يوجد مخزون متاح لهذا المنتج.")
                reserved = await session.execute(
                    update(DigitalInventoryItem)
                    .where(
                        DigitalInventoryItem.id == candidate.id,
                        DigitalInventoryItem.status == InventoryItemStatus.AVAILABLE,
                    )
                    .values(status=InventoryItemStatus.RESERVED)
                )
                if reserved.rowcount == 1:
                    item = candidate
                    item.status = InventoryItemStatus.RESERVED
                    break
            if item is None:
                raise InventoryError("تعذر حجز عنصر من المخزون، حاول مجدداً.")

            try:
                value = InventoryService.decrypt_value(item.encrypted_value)
            except InventoryError:
                raise InventoryError(
                    "تعذر فك تشفير عنصر المخزون؛ أوقف البيع وتحقق من المفتاح."
                ) from None

            metadata = None
            if item.metadata_json:
                try:
                    metadata = json.loads(item.metadata_json)
                except json.JSONDecodeError:
                    metadata = None

            user.balance -= price_usd
            user.total_spent_usd = (user.total_spent_usd or 0) + price_usd
            user.total_orders = (user.total_orders or 0) + 1
            order = UnifiedOrder(
                user_id=user_id,
                product_id=product_id,
                promotion_id=promotion_id,
                external_order_id=None,
                target=None,
                quantity=1,
                price_usd=price_usd,
                cost_price_usd=0,
                status=UnifiedOrderStatus.COMPLETED,
                status_message="تم التسليم تلقائياً من المخزون",
                result_data=json.dumps(
                    {"inventory_item_id": item.id, "metadata": metadata},
                    ensure_ascii=False,
                ),
                completed_at=datetime.utcnow(),
            )
            session.add(order)
            await session.flush()
            item.status = InventoryItemStatus.SOLD
            item.unified_order_id = order.id
            item.sold_at = datetime.utcnow()
            session.add(
                Transaction(
                    user_id=user_id,
                    type=TransactionType.PURCHASE,
                    amount=-price_usd,
                    balance_after=user.balance,
                    related_table="unified_orders",
                    related_id=order.id,
                    description=f"شراء منتج رقمي من المخزون #{product_id}",
                )
            )
            await session.commit()
            await session.refresh(order)
            return order, value, metadata

    @staticmethod
    async def void_item(session, item_id: int) -> bool:
        """إلغاء عنصر متاح بدون تسليمه (أداة أدمن)."""
        item = await session.get(DigitalInventoryItem, item_id)
        if item is None or item.status != InventoryItemStatus.AVAILABLE:
            return False
        item.status = InventoryItemStatus.VOID
        await session.commit()
        return True
