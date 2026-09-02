"""Global error guard for user/admin handlers.

If any button/message handler raises unexpectedly, the bot does not stay silent:
- user gets a friendly error message;
- private admin channel gets technical context plus a suggested fix.
"""

from __future__ import annotations

import html
import logging
import traceback

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from config import settings
from services.error_diagnosis_service import diagnose

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


def format_admin_error_report(
    exc: BaseException,
    *,
    event_name: str = "—",
    user_id: object = "—",
    username: str = "-",
    callback_data: object = "—",
    text: object = "—",
    tb: str = "",
) -> str:
    diagnosis = diagnose(exc, tb, context=f"{event_name} {callback_data or text or ''}")
    return (
        f"{diagnosis.as_html()}\n\n"
        "━━━━━━━━━━━━\n"
        f"الحدث: <code>{html.escape(str(event_name))}</code>\n"
        f"المستخدم: <code>{html.escape(str(user_id))}</code> "
        f"@{html.escape(username or '-')}\n"
        f"Callback: <code>{html.escape(str(callback_data or '—'))}</code>\n"
        f"Text: <code>{html.escape(str(text or '—')[:200])}</code>\n"
        f"الخطأ: <code>{html.escape(str(exc)[:500])}</code>\n\n"
        f"<pre>{html.escape((tb or '')[-2000:])}</pre>"
    )


async def report_exception_to_admin(bot, exc: BaseException, *, source: str = "background") -> None:
    """Send any unhandled error (handlers or background tasks) with a solution."""
    if not bot or not settings.ADMIN_NOTIFY_CHAT_ID:
        return
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-2500:]
    body = format_admin_error_report(exc, event_name=source, tb=tb)
    try:
        await bot.send_message(settings.ADMIN_NOTIFY_CHAT_ID, body, parse_mode="HTML")
    except Exception:
        logger.exception("Failed to report %s error to admin channel", source)


def install_asyncio_exception_handler(bot) -> None:
    """Forward unhandled asyncio task exceptions to the admin channel."""
    import asyncio

    loop = asyncio.get_running_loop()

    def _handler(loop, context):  # noqa: ANN001
        message = context.get("message", "Unhandled asyncio exception")
        exc = context.get("exception")
        logger.error("Asyncio error: %s", message, exc_info=exc)
        if exc is None:
            exc = RuntimeError(str(message))
        loop.create_task(report_exception_to_admin(bot, exc, source="asyncio"))

    loop.set_exception_handler(_handler)


class ErrorReportingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except Exception as exc:  # noqa: BLE001 - last-resort guard
            # ── أخطاء حميدة: نتجاهلها بهدوء دون إبلاغ الأدمن ──
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
            tb = traceback.format_exc()
            admin_text = format_admin_error_report(
                exc,
                event_name=event_name,
                user_id=getattr(user, "id", "—"),
                username=getattr(user, "username", "") or "-",
                callback_data=callback_data,
                text=text,
                tb=tb,
            )
            if bot and settings.ADMIN_NOTIFY_CHAT_ID:
                try:
                    await bot.send_message(
                        settings.ADMIN_NOTIFY_CHAT_ID, admin_text, parse_mode="HTML"
                    )
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
