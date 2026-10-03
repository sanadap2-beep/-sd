"""مهمة أسبوعية: تقرير الأدمن."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def weekly_admin_report_cycle(bot) -> None:
    """يرسل ملخص الأسبوع لقناة الأدمن."""
    try:
        from services.weekly_admin_report_service import WeeklyAdminReportService

        sent = await WeeklyAdminReportService.cycle(bot)
        if sent:
            logger.info('التقرير الأسبوعي: أُرسل')
    except Exception:  # noqa: BLE001 - المهمة الدورية لا تسقط البوت
        logger.exception('خطأ في دورة التقرير الأسبوعي')
