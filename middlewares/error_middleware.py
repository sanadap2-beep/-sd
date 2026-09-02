"""Global error guard for user/admin handlers.

If any button/message handler raises unexpectedly, the bot does not stay silent:
- user gets a friendly error message;
- private admin channel gets technical context.
"""

from __future__ import annotations

import html
import logging
import traceback

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from config import settings

logger = logging.getLogger(__name__)

# أخطاء تيليجرام "الحميدة": لا تستدعي إزعاج الأدمن ولا تُظهر تحذيراً للمستخدم.
# - message is not modified: ضغط المستخدم على نفس الزر والمحتوى لم يتغير.
# - query is too old: انتهت صلاحية نافذة الإجابة على الضغطة (أكثر من ~45 ثانية).
_BENIGN_TELEGRAM_ERRORS = (
    "message is not modified",
    "query is too old and response timeout expired",
    "QUERY_EXPIRED",
)


def _is_benign_telegram_error(exc: Exception) -> bool:
    """هل الخطأ خطأ تيليجرام غير ضار يمكن تجاهله بهدوء؟"""
    if not isinstance(exc, TelegramBadRequest):
        return False
    message = str(exc).lower()
    return any(marker.lower() in message for marker in _BENIGN_TELEGRAM_ERRORS)


class ErrorReportingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            # ── أخطاء حميدة: نتجاهلها بهدوء دون إبلاغ الأدمن ──
            # مثال: المستخدم ضغط نفس الزر مرتين والرسالة لم تتغير،
            # أو ضغط زراً قديماً انتهت صلاحية الإجابة عليه.
            if _is_benign_telegram_error(exc):
                logger.info(
                    "Benign Telegram error ignored (%s): %s",
                    type(event).__name__,
                    exc,
                )
                try:
                    if isinstance(event, CallbackQuery):
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