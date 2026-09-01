"""Global error guard for user/admin handlers.

If any button/message handler raises unexpectedly, the bot does not stay silent:
- user gets a friendly error message;
- private admin channel gets technical context.

الأخطاء "الحميدة" الشائعة في تيليجرام (الضغط المزدوج على الزر، انتهاء صلاحية
الـ callback، حظر المستخدم للبوت...) تُتجاهل بهدوء ولا تُرسل لقناة الأدمن،
حتى لا تمتلئ القناة بإنذارات لا معنى لها ولا يرى المستخدم رسالة خطأ بلا سبب.
"""

from __future__ import annotations

import asyncio
import html
import logging
import traceback

from aiogram import BaseMiddleware
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import CallbackQuery, Message

from config import settings

logger = logging.getLogger(__name__)

# رسائل تيليجرام التي لا تعتبر أعطالاً حقيقية
_BENIGN_BAD_REQUEST = (
    "message is not modified",
    "message to edit not found",
    "message to delete not found",
    "message can't be deleted",
    "message can't be edited",
    "query is too old",
    "query id is invalid",
    "response timeout expired",
    "message identifier is not specified",
    "there is no text in the message to edit",
)


class ErrorReportingMiddleware(BaseMiddleware):
    @staticmethod
    def _is_benign(exc: Exception) -> bool:
        if isinstance(exc, TelegramBadRequest):
            text = str(exc).lower()
            return any(marker in text for marker in _BENIGN_BAD_REQUEST)
        # المستخدم حظر البوت أو حذف المحادثة
        if isinstance(exc, TelegramForbiddenError):
            return True
        # مهلة/انقطاع شبكة مؤقت
        if isinstance(exc, (TelegramNetworkError, asyncio.TimeoutError)):
            return True
        return False

    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except TelegramRetryAfter as exc:
            # تجاوزنا حد الإرسال: ننتظر ثم نتجاهل هذا الحدث بدل إزعاج الأدمن.
            logger.warning("Flood control: retry after %ss", exc.retry_after)
            await asyncio.sleep(min(exc.retry_after, 5))
            return None
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            if self._is_benign(exc):
                logger.debug("Ignored benign Telegram error: %s", exc)
                if isinstance(event, CallbackQuery):
                    try:
                        await event.answer()
                    except Exception:
                        pass
                return None

            logger.exception("Unhandled bot handler error: %s", exc)
            bot = data.get("bot")
            user = getattr(event, "from_user", None)
            event_name = type(event).__name__
            callback_data = getattr(event, "data", None) if isinstance(event, CallbackQuery) else None
            text = getattr(event, "text", None) if isinstance(event, Message) else None
            tb = traceback.format_exc(limit=6)
            admin_text = (
                "🚨 <b>خطأ غير متوقع في البوت</b>\n\n"
                f"الحدث: <code>{html.escape(event_name)}</code>\n"
                f"المستخدم: <code>{getattr(user, 'id', '—')}</code> @{html.escape(getattr(user, 'username', '') or '-')}\n"
                f"Callback: <code>{html.escape(str(callback_data or '—'))}</code>\n"
                f"Text: <code>{html.escape(str(text or '—')[:200])}</code>\n"
                f"الخطأ: <code>{html.escape(str(exc)[:500])}</code>\n\n"
                f"<pre>{html.escape(tb[-2500:])}</pre>"
            )
            if bot and settings.ADMIN_NOTIFY_CHAT_ID:
                try:
                    await bot.send_message(settings.ADMIN_NOTIFY_CHAT_ID, admin_text, parse_mode="HTML")
                except Exception:
                    logger.exception("Failed to report handler error to admin channel")
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer("⚠️ حدث خطأ غير متوقع، تم إبلاغ الإدارة.", show_alert=True)
                elif isinstance(event, Message):
                    await event.answer("⚠️ حدث خطأ غير متوقع، تم إبلاغ الإدارة.")
            except Exception:
                pass
            return None
