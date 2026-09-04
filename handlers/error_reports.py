"""أزرار إشعارات الأخطاء المرسلة إلى محادثة/قناة الأدمن.

حالياً زر واحد: «تم تصليح الخطأ» — بعد معالجة الخطأ يضغطه الأدمن
فيتحذف إشعار الخطأ تلقائياً بدل بقائه مكدساً في المحادثة.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery

from config import settings
from middlewares.error_middleware import ERROR_FIXED_CALLBACK

logger = logging.getLogger(__name__)

router = Router(name="error_reports")


class CanDismissErrorReport(BaseFilter):
    """يسمح للأدمن فقط (من قاعدة البيانات أو من ADMIN_IDS في الإعدادات)."""

    async def __call__(self, event: CallbackQuery, db_user=None) -> bool:
        if db_user is not None and getattr(db_user, "is_admin", False):
            return True
        # fallback: مالكو البوت المذكورون في .env حتى لو لم يمرّوا بقاعدة البيانات.
        return event.from_user.id in settings.admin_ids_list


router.callback_query.filter(CanDismissErrorReport())


@router.callback_query(F.data == ERROR_FIXED_CALLBACK)
async def dismiss_error_report(callback: CallbackQuery):
    """«✅ تم تصليح الخطأ» → حذف إشعار الخطأ من محادثة الأدمن فوراً."""
    deleted = True
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        # الرسالة محذوفة مسبقاً أو قديمة — لا مشكلة.
        deleted = False
    except Exception:
        logger.exception("Failed to dismiss error report message")
        deleted = False
    if deleted:
        await callback.answer("🗑 تم حذف إشعار الخطأ.")
    else:
        await callback.answer("⚠️ تعذر حذف الإشعار (ربما حُذف مسبقاً).", show_alert=True)
