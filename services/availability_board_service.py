"""
التوفر المتقطع: قناة حية تنشر الدول النادرة لحظة رجوعها للمخزون.

النسخة السابقة كانت تعيد أرخص 10 دول في كل دورة؛ وهذا يعني أن الدول
الرخيصة الثابتة (إندونيسيا/كينيا/...) تبقى ظاهرة حتى لو لم يحدث أي شيء
جديد. هذه الخدمة تحتفظ بصورة آخر توفر معروف، ثم تنشر فقط الدول التي كانت
غير متاحة وأصبحت متاحة الآن (Restocked)، مع أولوية لقائمة الدول النادرة
التي يحددها الأدمن.

كل زر يبنى من يوزرنيم البوت الحقيقي عبر getMe عند توفر كائن bot، مع
تنظيف أي @ أو رابط كامل في قيمة البيئة احتياطياً، حتى لا تظهر مشكلة
«لم يتم العثور على اسم المستخدم» عند فتح الرابط.
"""

from __future__ import annotations

import logging
from datetime import datetime
from html import escape

from services.bot_identity import number_buy_start_link, resolve_bot_username
from services.feature_service import FeatureService
from services.number_catalog_service import (
    DEFAULT_INTERMITTENT_COUNTRY_CODES,
    BoardEntry,
    normalize_country_code,
)

logger = logging.getLogger(__name__)

FEATURE_KEY = "numbers_availability_board"


class AvailabilityBoardService:
    """بناء ونشر لوحة التوفر الحية في قناة الإدارة."""

    # (chat_id, message_id) آخر منشور — يُمحى ويُعاد إنشاؤه عند وجود تحديث.
    _last_post: tuple[int, int] | None = None
    # service_code -> آخر مجموعة دول كانت متاحة عند المزود.
    _last_available_by_service: dict[str, set[str]] = {}
    _last_skip_reason: str = ""

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
                "📡 <b>التوفر المتقطع — دول عادت للمخزون الآن</b>\n"
                "اضغط على الدولة لينقلك للبوت مباشرة واطلب رقمك.\n"
                "تُحدَّث هذه اللوحة تلقائياً كل دقيقة.",
            )
            or ""
        )

    @staticmethod
    async def watched_country_codes() -> list[str]:
        """قائمة الدول النادرة التي يراقبها الأدمن، مرتبة حسب الأولوية."""
        default = ",".join(sorted(DEFAULT_INTERMITTENT_COUNTRY_CODES))
        raw = await FeatureService.config(FEATURE_KEY, "watched_country_codes", default)
        if isinstance(raw, (list, tuple, set)):
            values = [str(item) for item in raw]
        else:
            values = str(raw or "").replace("\n", ",").replace(";", ",").split(",")
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            code = normalize_country_code(value)
            if code and code not in seen:
                seen.add(code)
                result.append(code)
        return result

    @staticmethod
    async def watched_country_codes_text(limit: int = 40) -> str:
        codes = await AvailabilityBoardService.watched_country_codes()
        shown = codes[:limit]
        suffix = " …" if len(codes) > limit else ""
        return ", ".join(shown) + suffix if shown else "—"

    @classmethod
    def reset_state(cls) -> None:
        """Test/maintenance helper: forget previous availability snapshots."""
        cls._last_post = None
        cls._last_available_by_service.clear()
        cls._last_skip_reason = ""

    # ─────────── اختيار الدول التي عادت للمخزون ───────────

    @staticmethod
    def _entry_key(entry: BoardEntry) -> str:
        return normalize_country_code(entry.code)

    @staticmethod
    def _looks_watched(entry: BoardEntry, watched: set[str]) -> bool:
        code = AvailabilityBoardService._entry_key(entry)
        if code in watched or getattr(entry, "is_intermittent", False):
            return True
        # احتياط للبيانات التي تأتي بأكواد مزودين غير موحدة لكن أسماؤها واضحة.
        name = normalize_country_code(str(entry.name_ar))
        return name in watched

    @classmethod
    def _rank_entries(
        cls,
        entries: list[BoardEntry],
        watched_order: list[str],
    ) -> list[BoardEntry]:
        """رتّب الدول النادرة حسب ترتيب الأدمن ثم السعر، لا حسب السعر فقط."""
        priority = {code: index for index, code in enumerate(watched_order)}

        def key(entry: BoardEntry):
            code = cls._entry_key(entry)
            watched_rank = priority.get(code)
            return (
                0 if watched_rank is not None or getattr(entry, "is_intermittent", False) else 1,
                watched_rank if watched_rank is not None else 10_000,
                entry.sell_usd,
                code,
            )

        return sorted(entries, key=key)

    @classmethod
    def _select_restocked(
        cls,
        service_code: str,
        entries: list[BoardEntry],
        top_n: int,
        watched_order: list[str],
    ) -> tuple[list[BoardEntry], bool]:
        """Return selected entries and whether they are real state changes.

        - عند أول تشغيل لا يوجد snapshot سابق؛ ننشر الدول المراقبة المتاحة الآن
          كبداية. إن كانت بيانات الاختبار/التثبيت لا تحتوي أي دولة مراقبة،
          نرجع لأول N للتوافق بدلاً من نشر لوحة فارغة.
        - بعد ذلك ننشر فقط الأكواد التي ظهرت في المخزون بعد أن كانت غائبة.
        """
        current_available = {cls._entry_key(entry) for entry in entries}
        previous = cls._last_available_by_service.get(service_code)
        cls._last_available_by_service[service_code] = set(current_available)

        watched = set(watched_order)
        by_code = {cls._entry_key(entry): entry for entry in entries}
        ranked_all = cls._rank_entries(entries, watched_order)
        watched_current = [entry for entry in ranked_all if cls._looks_watched(entry, watched)]

        if previous is None:
            # Bootstrap: انشر الدول النادرة المتاحة حالياً فقط. إذا مسح الأدمن
            # قائمة المراقبة عمداً، نستخدم كل الدول المتاحة كلوحة عامة.
            selected = watched_current if watched_order else ranked_all
            return selected[:top_n], False

        restocked_codes = current_available - previous
        if not restocked_codes:
            return [], True

        restocked_entries = [by_code[code] for code in restocked_codes if code in by_code]
        # عند وجود قائمة مراقبة لا ننشر الدول الرخيصة غير المطلوبة حتى لو ظهرت
        # للتو؛ الهدف تنبيه النادر فقط. إذا كانت القائمة فارغة عمداً ننشر أي
        # دولة عادت للمخزون.
        watched_restocked = [entry for entry in restocked_entries if cls._looks_watched(entry, watched)]
        selected = watched_restocked if watched_order else restocked_entries
        return cls._rank_entries(selected, watched_order)[:top_n], True

    # ─────────── البناء ───────────

    @classmethod
    async def build_rows(cls, bot=None) -> tuple[str, list[tuple[str, str]]] | None:
        """يعيد (نص اللوحة، [(تسمية الزر، رابط عميق)]) أو None إذا لا تحديث.

        الفحص الحي (use_cache=False) حتى تعكس اللوحة آخر تحديث للمزود.
        """
        from database.engine import async_session_maker
        from providers.countries import get_number_service_by_code
        from services.number_catalog_service import build_board, format_price

        cls._last_skip_reason = ""
        async with async_session_maker() as session:
            service_code = await cls.service_code()
            service = await get_number_service_by_code(session, service_code)
            if service is None or not service.is_active:
                cls._last_skip_reason = "service_unavailable"
                return None
            entries = await build_board(session, service, use_cache=False)

        if not entries:
            cls._last_available_by_service[service_code] = set()
            cls._last_skip_reason = "no_stock"
            return None

        bot_username = await resolve_bot_username(bot)
        if not bot_username:
            cls._last_skip_reason = "username_unresolved"
            logger.error("تعذّر تحديد يوزرنيم البوت لبناء روابط قناة التوفر.")
            return None

        watched_order = await cls.watched_country_codes()
        selected, real_change = cls._select_restocked(
            service_code,
            list(entries),
            await cls.top_n(),
            watched_order,
        )
        if not selected:
            cls._last_skip_reason = "no_new_restock"
            return None

        header = (await cls.header_text()).replace("{service}", escape(service.name_ar))
        status_line = (
            "🟢 <b>عادت هذه الدول للمخزون الآن.</b>"
            if real_change
            else "🟢 <b>دول نادرة مراقبة متاحة حالياً.</b>"
        )

        # الأزرار: رابط عميق يفتح البوت بطلب شراء مباشر لهذه الدولة.
        rows: list[tuple[str, str]] = []
        for entry in selected:
            price = format_price(entry.sell_usd)
            url = number_buy_start_link(bot_username, service.code, entry.code)
            if not url:
                continue
            rows.append((f"{entry.flag} {entry.name_ar} — {price}$", url))

        if not rows:
            return None

        stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        text = f"{header}\n\n{status_line}\n🕐 آخر تحديث: <code>{stamp}</code>\n"
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

        built = await cls.build_rows(bot=bot)
        if built is None:
            if cls._last_skip_reason == "no_new_restock":
                # لا نحذف آخر تنبيه Restock: بقاء آخر منشور في القناة أفضل من
                # إخفائه بعد دقيقة، والتنبيه الجديد سيستبدله عند حدوث Restock آخر.
                return "لا توجد دول نادرة عادت للمخزون الآن — بقي آخر تنبيه منشوراً."

            # عند نفاد المخزون كلياً أو تعطل الخدمة نمسح المنشور حتى لا يبقى
            # زر شراء لشيء غير متاح فعلاً.
            if cls._last_post and cls._last_post[0] == chat_id:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=cls._last_post[1])
                except Exception:
                    pass
                cls._last_post = None
            if cls._last_skip_reason == "username_unresolved":
                return "تعذّر تحديد يوزرنيم البوت لبناء روابط قناة التوفر."
            return "لا توجد دول متوفرة الآن — أزيلت الرسالة القديمة إن وُجدت."

        text, rows = built
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        bot_username = await resolve_bot_username(None)
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=label, url=url)] for label, url in rows]
            + ([[InlineKeyboardButton(text="📱 دخول البوت", url=f"https://t.me/{bot_username}")]] if bot_username else [])
        )

        # حذف المنشور السابق
        if cls._last_post and cls._last_post[0] == chat_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=cls._last_post[1])
            except Exception:
                logger.debug("تعذر حذف رسالة التوفر السابقة (ربما حُذفت يدوياً)")

        message = await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        cls._last_post = (chat_id, message.message_id)
        return f"✅ نُشرت لوحة التوفر ({len(rows)} دولة عادت/نادرة)."
