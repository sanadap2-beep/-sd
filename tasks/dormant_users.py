"""مهمة دورية: تذكير العملاء النائمين."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def dormant_users_cycle(bot) -> None:
    """يرسل «اشتقنا لك» للعملاء الذين غابوا عن المتجر."""
    try:
        from services.dormant_user_service import DormantUserService

        sent = await DormantUserService.cycle(bot)
        if sent:
            logger.info(f'تذكير العملاء النائمين: أُرسل إلى {sent} مستخدم')
    except Exception:  # noqa: BLE001 - المهمة الدورية لا تسقط البوت
        logger.exception('خطأ في دورة تذكير العملاء النائمين')
