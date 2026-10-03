"""مهمة دورية: كوبونات سلسلة الولاء."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def loyalty_chain_cycle(bot) -> None:
    """يمنح كوبونات العتبات الشهرية لمن أكثر من الطلب."""
    try:
        from services.loyalty_chain_service import LoyaltyChainService

        granted = await LoyaltyChainService.grant(bot)
        if granted:
            logger.info(f'سلسلة الولاء: مُنح {granted} كوبون')
    except Exception:  # noqa: BLE001 - المهمة الدورية لا تسقط البوت
        logger.exception('خطأ في دورة سلسلة الولاء')
