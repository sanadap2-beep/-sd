"""مهمة دورية: مراقبة صحة أرصدة المزودين والتنبيه المبكر."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def provider_health_cycle(bot) -> None:
    """يحدّث أرصدة المزودين وينبّه الأدمن قبل نفادها."""
    try:
        from services.provider_health_service import ProviderHealthService

        sent = await ProviderHealthService.cycle(bot)
        if sent:
            logger.info(f'صحة المزودين: أُرسل {sent} تنبيه رصيد')
    except Exception:  # noqa: BLE001 - المهمة الدورية لا تسقط البوت
        logger.exception('خطأ في دورة صحة المزودين')
