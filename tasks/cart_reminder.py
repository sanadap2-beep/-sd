"""مهمة دورية: تذكير أصحاب السلات المتروكة."""

from __future__ import annotations

import logging

from services.cart_reminder_service import CartReminderService

logger = logging.getLogger(__name__)


async def cart_reminder_cycle(bot) -> None:
    """دورة التذكير — تُشغَّل من المجدول."""
    try:
        sent = await CartReminderService.cycle(bot)
    except Exception:  # noqa: BLE001 - دورة خلفية لا تُسقط البوت
        logger.exception("فشلت دورة تذكير السلة")
        return
    if sent:
        logger.info("cart_reminder: أُرسل %d تذكير سلة.", sent)
