"""حارس المخزون: تنبيه عند النفاد + إخفاء تلقائي + إعادة تلقائية.

المشكلة: منتج «مخزون رقمي» ينفد رصيده وبقى معروضاً في المتجر،
فيشتري الزبون ويفشل التسليم — أسوأ تجربة ممكنة.

الحل:
1. كل دورة نعدّ العناصر المتاحة لكل منتج مخزون.
2. تحت حد «القليل» (افتراضياً ٣) ⇒ تنبيه للأدمن.
3. عند الصفر ⇒ إخفاء المنتج تلقائياً عن المتجر + تنبيه،
   وتذكّر من أخفيناه حتى نعيده تلقائياً متى عاد المخزون.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import func, select

from database.models import (
    DigitalInventoryItem,
    InventoryItemStatus,
    Product,
    ProductFulfillmentType,
    ProductStatus,
)
from services.feature_service import FeatureService
from services.html_guard import esc
from services.settings_service import SettingsService

logger = logging.getLogger(__name__)

FEATURE_KEY = "stock_guard"
HIDDEN_IDS_KEY = "stock_guard_hidden_ids"


class InventoryGuardService:
    """فحص المخزون: تحذير → إخفاء → إعادة."""

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled(FEATURE_KEY, default=True)

    @staticmethod
    async def low_threshold() -> int:
        value = await FeatureService.config_int(FEATURE_KEY, "low_stock_threshold", 3)
        try:
            return max(1, min(int(value), 100))
        except (TypeError, ValueError):
            return 3

    @staticmethod
    async def auto_hide() -> bool:
        return await FeatureService.config_bool(FEATURE_KEY, "auto_hide", True)

    @staticmethod
    async def hidden_ids(session) -> set[int]:
        raw = await SettingsService.get(HIDDEN_IDS_KEY, "")
        if not raw:
            return set()
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return set()
        return {int(item) for item in data if str(item).isdigit()}

    @staticmethod
    async def _save_hidden(session, ids: set[int]) -> None:
        await SettingsService.set(session, HIDDEN_IDS_KEY, json.dumps(sorted(ids)))

    @classmethod
    async def stock_rows(cls, session) -> list[dict]:
        """كل منتجات المخزون (المخفية منها أيضاً) مع عدد المتاح."""
        products = (
            (
                await session.execute(
                    select(Product).where(
                        Product.fulfillment_type == ProductFulfillmentType.INVENTORY
                    )
                )
            )
            .scalars()
            .all()
        )
        if not products:
            return []
        ids = [p.id for p in products]
        counts = dict(
            (
                await session.execute(
                    select(
                        DigitalInventoryItem.product_id,
                        func.count(DigitalInventoryItem.id),
                    )
                    .where(
                        DigitalInventoryItem.product_id.in_(ids),
                        DigitalInventoryItem.status == InventoryItemStatus.AVAILABLE,
                    )
                    .group_by(DigitalInventoryItem.product_id)
                )
            ).all()
        )
        return [
            {
                "id": p.id,
                "name": p.name_ar,
                "available": int(counts.get(p.id, 0)),
                "is_visible": p.status == ProductStatus.ACTIVE,
                "product": p,
            }
            for p in products
        ]

    @classmethod
    def compose_empty_alert(cls, name: str, hidden: bool) -> str:
        line = (
            "🙈 <b>أُخفي تلقائياً</b> من المتجر حتى تزوّد المخزون."
            if hidden
            else "⚠️ ما زال معروضاً (الإخفاء التلقائي مطفأ)."
        )
        return (
            "📦 <b>نفد المخزون</b>\n\n"
            f"المنتج: <b>{esc(name)}</b>\n"
            "العناصر المتاحة: <b>٠</b>\n"
            f"{line}\n\n"
            "أضف عناصر جديدة: لوحة التحكم ← 📦 المخزون الرقمي."
        )

    @staticmethod
    def compose_low_alert(name: str, available: int) -> str:
        return (
            "🟡 <b>مخزون على وشك النفاد</b>\n\n"
            f"المنتج: <b>{esc(name)}</b>\n"
            f"المتبقي: <b>{available}</b> عنصر فقط.\n\n"
            "زوّده الآن حتى لا يتوقف البيع."
        )

    @staticmethod
    def compose_restored_alert(name: str, available: int) -> str:
        return (
            "✅ <b>عاد المنتج للمتجر</b>\n\n"
            f"<b>{esc(name)}</b> صار متوفراً ({available} عنصر) "
            "فأعدنا عرضه تلقائياً."
        )

    @classmethod
    async def cycle(cls, bot) -> dict:
        """يفحص المخزون: ينبّه، يُخفي النافد، ويعيد المتوفر."""
        result = {"warned": 0, "hidden": 0, "restored": 0}
        if not await cls.enabled():
            return result
        from database.engine import async_session_maker
        from services.notification_service import NotificationService

        threshold = await cls.low_threshold()
        hide = await cls.auto_hide()
        notifier = NotificationService(bot)
        today = datetime.utcnow().strftime("%Y-%m-%d")

        async with async_session_maker() as session:
            rows = await cls.stock_rows(session)
            result["checked"] = len(rows)
            hidden = await cls.hidden_ids(session)

            empty: list[dict] = []
            low: list[dict] = []
            restored: list[dict] = []
            for row in rows:
                available = row["available"]

                # إعادة تلقائية لمن كنا أخفيناه وعاد مخزونه
                if row["id"] in hidden and available > 0:
                    row["product"].status = ProductStatus.ACTIVE
                    restored.append(row)
                    continue

                # منتج مخفي أصلاً (يدوياً أو سبق إخفاؤه): لا نكرر تنبيهه كل دورة
                if not row["is_visible"]:
                    continue

                if available <= 0:
                    empty.append(row)
                elif available <= threshold:
                    low.append(row)

            # إخفاء النافد
            for row in empty:
                if hide and row["is_visible"]:
                    row["product"].status = ProductStatus.INACTIVE
                    hidden.add(row["id"])
                    result["hidden"] += 1
            if rows:
                await session.commit()
            await cls._save_hidden(session, hidden)
            await session.commit()

            names = {row["id"]: row["name"] for row in rows}
            counts = {row["id"]: row["available"] for row in rows}
            restored_payload = [(row["id"], row["name"], row["available"]) for row in restored]
            low_payload = [(row["id"], row["name"], row["available"]) for row in low]
            empty_payload = [(row["id"], row["name"], hide) for row in empty]

        for product_id, name, hide_flag in empty_payload:
            try:
                ok = await notifier.notify_admin(
                    cls.compose_empty_alert(name, hide_flag),
                    dedupe_key=f"stock_empty:{product_id}:{today}",
                )
            except Exception:  # noqa: BLE001
                logger.debug("تعذّر إرسال تنبيه نفاد مخزون %s", product_id)
                continue
            result["warned"] += int(bool(ok))

        for product_id, name, available in low_payload:
            try:
                ok = await notifier.notify_admin(
                    cls.compose_low_alert(name, available),
                    dedupe_key=f"stock_low:{product_id}:{today}",
                )
            except Exception:  # noqa: BLE001
                logger.debug("تعذّر إرسال تنبيه مخزون منخفض %s", product_id)
                continue
            result["warned"] += int(bool(ok))

        for product_id, name, available in restored_payload:
            try:
                ok = await notifier.notify_admin(
                    cls.compose_restored_alert(name, available),
                    dedupe_key=f"stock_restored:{product_id}:{today}",
                )
            except Exception:  # noqa: BLE001
                logger.debug("تعذّر إرسال تنبيه رجوع مخزون %s", product_id)
                continue
            result["restored"] += int(bool(ok))

        _ = names, counts
        return result
