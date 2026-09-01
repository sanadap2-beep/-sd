"""مراقبة تغيّر أسعار ومخزون المنتجات."""

import logging

from services.favorites_stock_service import FavoritesStockService

logger = logging.getLogger(__name__)


async def check_product_watches(bot):
    try:
        sent = await FavoritesStockService.check_all(bot)
        if sent:
            logger.info("تم إرسال %s تنبيه منتج.", sent)
    except Exception:
        logger.exception("فشل فحص تنبيهات المنتجات")
