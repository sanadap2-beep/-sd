"""
التوفر المتقطع: قناة حية تعرض أعلى الدول الجاهزة للطلب فوراً.

كل دورة (افتراضياً 60 ثانية):
1) سحب أسعار حية من المزودين (بلا كاش) للخدمة المحددة.
2) ترتيب الدول المتوفرة من الأرخص للأغلى وأخذ أول N.
3) حذف رسالة القناة السابقة ونشر رسالة جديدة بأزرار روابط عميقة
   (t.me/<البوت>?start=buy_<service>__<country>) — الضغط عليها
   ينقل المستخدم للبوت مباشرة ويطلب الرقم فوراً.

الفائدة: المستخدم لا يضغط على دولة قد تكون نفدت بعد ساعة — يرى
ما هو متوفر الآن فعلاً، والقائمة تتغير لحظياً عند توفر دول فجأة.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from html import escape

from config import settings
from services.feature_service import FeatureService

logger = logging.getLogger(__name__)

FEATURE_KEY = "numbers_availability_board"


class AvailabilityBoardService:
    """بناء ونشر لوحة التوفر الحية في قناة الإدارة."""

    # (chat_id, message_id) آخر منشور — يُمحى ويُعاد إنشاؤه كل دورة.
    _last_post: tuple[int, int] | None = None

    # ─────────── الإعدادات ───────────

    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled(FEATURE_KEY)

    @staticmethod
    async def channel_chat_id() -> int | None:
        raw = str(await FeatureService.config(FEATURE_KEY, "channel_chat_id", "") or "").strip()
        if not raw or raw == "0":
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    @staticmethod
    async def service_code() -> str:
        return str(await FeatureService.config(FEATURE_KEY, "service_code", "whatsapp") or "whatsapp")

    @staticmethod
    async def top_n() -> int:
        try:
            value = int(await FeatureService.config(FEATURE_KEY, "top_n", 10))
        except (TypeError, ValueError):
            value = 10
        return max(3, min(value, 25))

    @staticmethod
    async def refresh_seconds() -> int:
        try:
            value = int(await FeatureService.config(FEATURE_KEY, "refresh_seconds", 60))
        except (TypeError, ValueError):
            value = 60
        return max(30, min(value, 300))

    @staticmethod
    async def header_text() -> str:
        return str(
            await FeatureService.config(
                FEATURE_KEY,
                "header_text",
                "📡 <b>التوفر المتقطع — دول جاهزة للطلب فوراً</b>\n"
                "اضغط على الدولة لينقلك للبوت مباشرة واطلب رقمك.\n"
                "تُحدَّث هذه اللوحة تلقائياً كل دقيقة.",
            )
            or ""
        )

    # ─────────── البناء ───────────

    @classmethod
    async def build_rows(cls) -> tuple[str, list[tuple[str, str]]] | None:
        """يعيد (نص اللوحة، [(تسمية الزر، رابط عميق)]) أو None إذا لم تتوفر دول.

        الفحص الحية (use_cache=False) حتى تعكس اللوحة آخر تحديث للمزود.
        """
        from database.engine import async_session_maker
        from providers.countries import get_number_service_by_code
        from services.number_catalog_service import build_board, format_price

        async with async_session_maker() as session:
            service_code = await cls.service_code()
            service = await get_number_service_by_code(session, service_code)
            if service is None or not service.is_active:
                return None
            entries = await build_board(session, service, use_cache=False)

        top = entries[: await cls.top_n()]
        if not top:
            return None

        bot_username = settings.BOT_USERNAME
        header = await cls.header_text()
        header = header.replace("{service}", service.name_ar)

        # الأزرار: رابط عميق يفتح البوت بطلب شراء مباشر لهذه الدولة.
        rows: list[tuple[str, str]] = []
        for entry in top:
            price = format_price(entry.sell_usd)
            rows.append(
                (
                    f"{entry.flag} {entry.name_ar} — {price}$",
                    f"https://t.me/{bot_username}?start=buy_{service.code}__{entry.code}",
                )
            )

        stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        text = f"{header}\n\n🕐 آخر تحديث: <code>{stamp}</code>\n"
        text += "💰 الأسعار بالبيع النهائي (شاملة هامش الربح).\n"
        text += "🛡 إذا لم يتوفر الرقم بعد انتقالك فأي مبلغ يُخصم يُسترجع فوراً."
        return text, rows

    # ─────────── النشر ───────────

    @classmethod
    async def post_board(cls, bot) -> str:
        """يحذف المنشور السابق وينشر اللوحة الجديدة. يعيد رسالة الحالة."""
        if not await cls.enabled():
            return "الميزة معطلة من مركز الإضافات."
        chat_id = await cls.channel_chat_id()
        if chat_id is None:
            return "لم تُضبط قناة التوفر بعد (من إدارة خدمات الأرقام ← 📡 قناة التوفر)."

        built = await cls.build_rows()
        if built is None:
            # لا دول متوفرة الآن: نمسح المنشور السابق حتى لا يظهر سعر قديم.
            if cls._last_post and cls._last_post[0] == chat_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=cls._last_post[1])
                except Exception:
                    pass
                cls._last_post = None
            return "لا توجد دول متوفرة الآن — أزيلت الرسالة القديمة إن وُجدت."

        text, rows = built
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=label, url=url)] for label, url in rows]
            + [[InlineKeyboardButton(text="📱 دخول البوت", url=f"https://t.me/{settings.BOT_USERNAME}")]]
        )

        # حذف المنشور السابق
        if cls._last_post and cls._last_post[0] == chat_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=cls._last_post[1])
            except Exception:
                logger.debug("تعذر حذف رسالة التوفر السابقة (ربما حُذفت يدوياً)")

        message = await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        cls._last_post = (chat_id, message.message_id)
        return f"✅ نُشرت اللوحة في القناة ({len(rows)} دولة)."
