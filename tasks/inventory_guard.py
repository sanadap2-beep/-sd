"""مهمة دورية: حارس المخزون (نفاد / إخفاء / إعادة)."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def inventory_guard_cycle(bot) -> None:
    """يفحص مخزون المنتجات الرقمية وينبّه عند النفاد."""
    try:
        from services.inventory_guard_service import InventoryGuardService

        result = await InventoryGuardService.cycle(bot)
        if result.get("hidden") or result.get("warned"):
            logger.info(
                f'حارس المخزون: {result.get("hidden", 0)} مخفي · '
                f'{result.get("warned", 0)} تنبيه · {result.get("restored", 0)} مُعاد'
            )
    except Exception:  # noqa: BLE001 - المهمة الدورية لا تسقط البوت
        logger.exception('خطأ في دورة حارس المخزون')
