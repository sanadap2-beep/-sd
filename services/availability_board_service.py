"""
📡 التوفر المتقطع — قناة أرقام حية فعلاً.

ما كان مكسوراً في النسخة السابقة:

1. **اللوحة «ما بتتحدث»**: كانت تنشر فقط عند وجود Restock حقيقي، وترجع
   ``no_new_restock`` في كل دورة أخرى. النتيجة: منشور واحد يجمّد في القناة
   لساعات. الآن اللوحة تُحدَّث في كل دورة (تعديل نفس الرسالة عبر
   ``edit_message_text`` بدل حذف/إعادة نشر)، ويُعاد ترتيب الدول الثابتة
   بشكل دوّار (rotation) فتبدو القناة حيّة، بينما يبقى التمييز الحقيقي
   محفوظاً لدول الـ Restock.

2. **الدول «ما بتتغير»**: كان snapshot المقارنة في الذاكرة فقط، فيضيع مع كل
   إعادة تشغيل، ولم يكن هناك أي تتبّع لتاريخ التوفر. الآن الحالة تُحفَظ في
   جدول الإعدادات (``SettingsService``) فتنجو من إعادة التشغيل، ومعها
   ``last_seen`` لكل دولة حتى نميّز «عادت الآن» عن «متوفرة من زمان».

3. **«اسم المستخدم غير موجود» عند الضغط على دولة**: الرابط كان يُبنى أحياناً
   من ``resolve_bot_username(None)`` (قيمة البيئة المخزّنة/الفارغة) بدل
   يوزرنيم ``getMe`` الحيّ. الآن كل الروابط — بما فيها زر «دخول البوت» —
   تُبنى من نفس اليوزرنيم الحيّ المُتحقَّق منه، ولا تُنشر اللوحة أصلاً إذا
   تعذّر التحقق منه.

4. **القناة «ما بتوصل»**: التعديل في مكان الرسالة لا يُصدر إشعاراً في
   Telegram، والرسالة تبقى مدفونة تحت أي منشور أحدث. الآن تُعاد اللوحة
   كرسالة جديدة دورياً (``auto_repost`` كل ``repost_every_cycles`` دورة،
   افتراضياً 10 دورات ≈ 10 دقائق)، فيصل المشتركين إشعار فعلي وتبقى
   اللوحة ظاهرة بأعلى القناة. واختيارياً ``repost_on_restock`` يرسل
   رسالة جديدة فور رجوع أي دولة نادرة.

5. **ضغط دولة ثم «لا يوجد رقم»**: قبل النشر يُتحقَّق من أن الدولة موجودة
   ومفعّلة في قاعدة البيانات (``is_active``)، فلا يظهر زر لدولة معطّلة.
   والدول التي نفدت للتو تُعرَض تلقائياً كـ «نفدت» بدل رابط شراء ميت.
"""

from __future__ import annotations

import json
import logging
import time
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

# مفتاح تخزين الحالة الدائمة (ينجو من إعادة تشغيل البوت).
STATE_SETTING_PREFIX = "availability_board_state"

# كم ثانية تبقى الدولة موسومة بـ «عادت الآن 🔥» بعد رصد عودتها.
RESTOCK_HIGHLIGHT_SECONDS = 600

BADGE_RESTOCK = "🔥"
BADGE_RARE = "💎"
BADGE_STABLE = "🟢"


class AvailabilityBoardService:
    """بناء ونشر لوحة التوفر الحية في قناة عامة."""

    # (chat_id, message_id) آخر منشور — يُعدَّل في مكانه ما دام حياً.
    _last_post: tuple[int, int] | None = None
    # service_code -> {country_code: {"last_seen": ts, "restocked_at": ts}}
    _availability_state: dict[str, dict[str, dict[str, float]]] = {}
    # service_code -> عدّاد الدورات (لتدوير ترتيب الدول الثابتة).
    _cycle: dict[str, int] = {}
    # service_code -> عدد الدورات منذ آخر «رسالة جديدة» (لإعادة النشر الدوري).
    _cycles_since_post: dict[str, int] = {}
    # عدد الدول التي عادت للمخزون في آخر دورة (للإشعار الفوري).
    _last_restock_count: int = 0
    _state_loaded: set[str] = set()
    _last_skip_reason: str = ""
    _last_text: str = ""

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
            value = int(await FeatureService.config(FEATURE_KEY, "top_n", 12))
        except (TypeError, ValueError):
            value = 12
        return max(3, min(value, 25))

    @staticmethod
    async def refresh_seconds() -> int:
        try:
            value = int(await FeatureService.config(FEATURE_KEY, "refresh_seconds", 60))
        except (TypeError, ValueError):
            value = 60
        return max(30, min(value, 300))

    @staticmethod
    async def rotate_stable() -> bool:
        """تدوير ترتيب الدول الثابتة كل دورة (إحساس بالحركة)."""
        return await FeatureService.config_bool(FEATURE_KEY, "rotate_stable", True)

    @staticmethod
    async def always_publish() -> bool:
        """نشر/تحديث اللوحة في كل دورة حتى لو لم يحدث Restock."""
        return await FeatureService.config_bool(FEATURE_KEY, "always_publish", True)

    @staticmethod
    async def auto_repost() -> bool:
        """«إعادة النشر التلقائي»: كل عدد محدد من الدورات تُنشر اللوحة
        كرسالة جديدة بدل تعديل القديمة.

        Telegram لا يُصدر إشعاراً عند تعديل رسالة، والرسالة المُعدَّلة تبقى
        مدفونة تحت أي منشور أحدث. إعادة النشر الدورية تُبقي اللوحة في أعلى
        القناة وتُشعر المشتركين بها فعلاً.
        """
        return await FeatureService.config_bool(FEATURE_KEY, "auto_repost", True)

    @staticmethod
    async def repost_every_cycles() -> int:
        """كم دورة تحديث بين كل إعادة نشر (رسالة جديدة)."""
        try:
            value = int(await FeatureService.config(FEATURE_KEY, "repost_every_cycles", 10))
        except (TypeError, ValueError):
            value = 10
        return max(1, min(value, 240))

    @staticmethod
    async def repost_on_restock() -> bool:
        """إشعار فوري (رسالة جديدة) لحظة رجوع دولة نادرة للمخزون."""
        return await FeatureService.config_bool(FEATURE_KEY, "repost_on_restock", False)

    @staticmethod
    async def header_text() -> str:
        return str(
            await FeatureService.config(
                FEATURE_KEY,
                "header_text",
                "📡 <b>التوفر المتقطع — أرقام {service}</b>\n"
                "🔥 = دولة نادرة عادت للمخزون الآن · 💎 = نادرة متاحة · 🟢 = متوفرة\n"
                "اضغط على الدولة لينقلك البوت مباشرة لإتمام الطلب.",
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
        cls._availability_state.clear()
        cls._cycle.clear()
        cls._cycles_since_post.clear()
        cls._last_restock_count = 0
        cls._state_loaded.clear()
        cls._last_skip_reason = ""
        cls._last_text = ""

    # ─────────── الحالة الدائمة ───────────

    @classmethod
    async def _load_state(cls, service_code: str) -> dict[str, dict[str, float]]:
        """يحمّل صورة التوفر السابقة من قاعدة البيانات مرة واحدة لكل خدمة."""
        if service_code in cls._state_loaded:
            return cls._availability_state.setdefault(service_code, {})

        cls._state_loaded.add(service_code)
        state: dict[str, dict[str, float]] = {}
        try:
            from services.settings_service import SettingsService

            raw = await SettingsService.get(f"{STATE_SETTING_PREFIX}:{service_code}", "")
            parsed = json.loads(raw) if raw else {}
            if isinstance(parsed, dict):
                for code, meta in parsed.items():
                    if isinstance(meta, dict):
                        state[normalize_country_code(code)] = {
                            "last_seen": float(meta.get("last_seen", 0) or 0),
                            "restocked_at": float(meta.get("restocked_at", 0) or 0),
                        }
        except Exception:  # noqa: BLE001 - الحالة المفقودة ليست خطأً قاتلاً
            logger.debug("تعذّر تحميل حالة لوحة التوفر لـ %s", service_code)

        cls._availability_state[service_code] = state
        return state

    @classmethod
    async def _save_state(cls, service_code: str) -> None:
        state = cls._availability_state.get(service_code) or {}
        try:
            from database.engine import async_session_maker
            from services.settings_service import SettingsService

            payload = json.dumps(
                {
                    code: {
                        "last_seen": round(meta.get("last_seen", 0), 1),
                        "restocked_at": round(meta.get("restocked_at", 0), 1),
                    }
                    for code, meta in state.items()
                },
                ensure_ascii=False,
            )
            async with async_session_maker() as session:
                await SettingsService.set(
                    session, f"{STATE_SETTING_PREFIX}:{service_code}", payload
                )
        except Exception:  # noqa: BLE001
            logger.debug("تعذّر حفظ حالة لوحة التوفر لـ %s", service_code)

    # ─────────── التصنيف والترتيب ───────────

    @staticmethod
    def _entry_key(entry: BoardEntry) -> str:
        return normalize_country_code(entry.code)

    @staticmethod
    def _looks_watched(entry: BoardEntry, watched: set[str]) -> bool:
        code = AvailabilityBoardService._entry_key(entry)
        if code in watched or getattr(entry, "is_intermittent", False):
            return True
        # احتياط للبيانات التي تأتي بأكواد مزودين غير موحدة لكن أسماؤها واضحة.
        return normalize_country_code(str(entry.name_ar)) in watched

    @classmethod
    def _update_snapshot(
        cls,
        service_code: str,
        entries: list[BoardEntry],
        state: dict[str, dict[str, float]],
        now: float,
    ) -> set[str]:
        """يحدّث الصورة ويعيد أكواد الدول التي عادت للمخزون في هذه الدورة."""
        current = {cls._entry_key(entry) for entry in entries}
        first_run = not state
        restocked: set[str] = set()

        for code in current:
            meta = state.get(code)
            if meta is None:
                # أول تشغيل: لا نضجّ القناة بتنبيهات Restock وهمية لكل الدول.
                if not first_run:
                    restocked.add(code)
                    state[code] = {"last_seen": now, "restocked_at": now}
                else:
                    state[code] = {"last_seen": now, "restocked_at": 0.0}
                continue
            gap = now - float(meta.get("last_seen", 0) or 0)
            # غابت لأكثر من دورتين ثم رجعت → Restock حقيقي.
            if gap > 0 and meta.get("last_seen") and gap > cls._restock_gap_seconds:
                restocked.add(code)
                meta["restocked_at"] = now
            meta["last_seen"] = now

        # تنظيف الدول التي غابت طويلاً جداً حتى لا ينمو التخزين بلا حدود.
        stale_cutoff = now - 7 * 24 * 3600
        for code in [c for c, m in state.items() if float(m.get("last_seen", 0)) < stale_cutoff]:
            state.pop(code, None)

        cls._availability_state[service_code] = state
        return restocked

    # فجوة الغياب التي تُعتبر بعدها العودة «Restock» (تُضبط ديناميكياً).
    _restock_gap_seconds: float = 90.0

    @classmethod
    def _badge_for(
        cls,
        entry: BoardEntry,
        watched: set[str],
        state: dict[str, dict[str, float]],
        now: float,
    ) -> str:
        code = cls._entry_key(entry)
        meta = state.get(code) or {}
        restocked_at = float(meta.get("restocked_at", 0) or 0)
        if restocked_at and (now - restocked_at) <= RESTOCK_HIGHLIGHT_SECONDS:
            return BADGE_RESTOCK
        if cls._looks_watched(entry, watched):
            return BADGE_RARE
        return BADGE_STABLE

    @classmethod
    def _order_entries(
        cls,
        service_code: str,
        entries: list[BoardEntry],
        watched_order: list[str],
        state: dict[str, dict[str, float]],
        now: float,
        rotate: bool,
    ) -> list[BoardEntry]:
        """🔥 أولاً، ثم 💎 النادرة حسب ترتيب الأدمن، ثم الثابتة بترتيب دوّار."""
        priority = {code: index for index, code in enumerate(watched_order)}
        watched = set(watched_order)

        hot: list[BoardEntry] = []
        rare: list[BoardEntry] = []
        stable: list[BoardEntry] = []
        for entry in entries:
            badge = cls._badge_for(entry, watched, state, now)
            (hot if badge == BADGE_RESTOCK else rare if badge == BADGE_RARE else stable).append(entry)

        def rare_key(entry: BoardEntry):
            code = cls._entry_key(entry)
            return (priority.get(code, 10_000), entry.sell_usd, code)

        def hot_key(entry: BoardEntry):
            meta = state.get(cls._entry_key(entry)) or {}
            # الأحدث عودةً أولاً.
            return (-float(meta.get("restocked_at", 0) or 0), *rare_key(entry))

        hot.sort(key=hot_key)
        rare.sort(key=rare_key)
        stable.sort(key=lambda e: (e.sell_usd, cls._entry_key(e)))

        if rotate and stable:
            # تدوير: نفس الدول لكن نقطة البداية تتغير كل دورة، فتبدو اللوحة
            # متحركة للمشترك دون أي ادعاء كاذب بتغيّر المخزون.
            step = cls._cycle.get(service_code, 0) % len(stable)
            stable = stable[step:] + stable[:step]

        return hot + rare + stable

    # ─────────── البناء ───────────

    @classmethod
    async def build_rows(cls, bot=None) -> tuple[str, list[tuple[str, str]]] | None:
        """يعيد (نص اللوحة، [(تسمية الزر، رابط عميق)]) أو None عند التعذر.

        الفحص الحي (use_cache=False) حتى تعكس اللوحة آخر تحديث للمزود.
        """
        from database.engine import async_session_maker
        from providers.countries import get_number_service_by_code
        from services.number_catalog_service import build_board, format_price

        cls._last_skip_reason = ""
        cls._last_restock_count = 0
        now = time.time()

        async with async_session_maker() as session:
            service_code = await cls.service_code()
            service = await get_number_service_by_code(session, service_code)
            if service is None or not service.is_active:
                cls._last_skip_reason = "service_unavailable"
                return None
            entries = list(await build_board(session, service, use_cache=False))
            entries = await cls._filter_active_countries(session, entries)

        state = await cls._load_state(service_code)

        if not entries:
            # لا نمسح التاريخ: بقاء last_seen هو ما يسمح برصد العودة لاحقاً.
            cls._last_skip_reason = "no_stock"
            await cls._save_state(service_code)
            return None

        bot_username = await resolve_bot_username(bot)
        if not bot_username:
            cls._last_skip_reason = "username_unresolved"
            logger.error("تعذّر تحديد يوزرنيم البوت لبناء روابط قناة التوفر.")
            return None

        # فجوة الـ Restock = ضعف دورة التحديث (يتحمّل دورة فاشلة واحدة).
        cls._restock_gap_seconds = max(90.0, (await cls.refresh_seconds()) * 2.5)

        restocked = cls._update_snapshot(service_code, entries, state, now)
        cls._last_restock_count = len(restocked)
        await cls._save_state(service_code)

        watched_order = await cls.watched_country_codes()
        ordered = cls._order_entries(
            service_code,
            entries,
            watched_order,
            state,
            now,
            await cls.rotate_stable(),
        )
        selected = ordered[: await cls.top_n()]
        cls._cycle[service_code] = cls._cycle.get(service_code, 0) + 1

        watched = set(watched_order)
        rows: list[tuple[str, str]] = []
        hot_count = 0
        for entry in selected:
            url = number_buy_start_link(bot_username, service.code, entry.code)
            if not url:
                continue
            badge = cls._badge_for(entry, watched, state, now)
            if badge == BADGE_RESTOCK:
                hot_count += 1
            price = format_price(entry.sell_usd)
            suffix = " · عادت الآن" if badge == BADGE_RESTOCK else ""
            rows.append((f"{badge} {entry.flag} {entry.name_ar} — {price}${suffix}", url))

        if not rows:
            cls._last_skip_reason = "no_links"
            return None

        header = (await cls.header_text()).replace("{service}", escape(service.name_ar))
        if hot_count:
            status_line = f"🔥 <b>{hot_count} دولة نادرة عادت للمخزون الآن — الكمية تنفد بسرعة.</b>"
        elif restocked:
            status_line = "🟢 <b>تحديث مباشر: تغيّر المخزون للتو.</b>"
        else:
            status_line = "🟢 <b>هذه الدول متاحة الآن للطلب الفوري.</b>"

        stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        text = (
            f"{header}\n\n{status_line}\n"
            f"🌍 متاح الآن: <b>{len(entries)}</b> دولة · معروض: <b>{len(rows)}</b>\n"
            f"🕐 آخر فحص: <code>{stamp}</code>\n"
            "💰 الأسعار نهائية شاملة الرسوم.\n"
            "🛡 إن لم يصلك الكود يُسترجع المبلغ تلقائياً."
        )
        return text, rows

    @staticmethod
    async def _filter_active_countries(session, entries: list[BoardEntry]) -> list[BoardEntry]:
        """يستبعد أي دولة غير موجودة/معطّلة في قاعدة البيانات.

        هذا ما يمنع «ضغطت على الدولة فقال لا يوجد رقم»: الزر لا يُبنى أصلاً
        لدولة لا يستطيع البوت بيعها.
        """
        if not entries:
            return []
        try:
            from sqlalchemy import select

            from database.models import Country

            result = await session.execute(select(Country.code, Country.is_active))
            known = {normalize_country_code(code): bool(active) for code, active in result.all()}
        except Exception:  # noqa: BLE001
            return entries
        # نستبعد فقط ما نعرف يقيناً أنه معطّل؛ الأكواد غير المسجّلة تُترك كما هي
        # حتى لا نُفرغ اللوحة بسبب اختلاف صيغة أكواد المزوّد.
        return [e for e in entries if known.get(normalize_country_code(e.code), True)]

    # ─────────── النشر ───────────

    @classmethod
    async def post_board(cls, bot) -> str:
        """ينشر اللوحة أو يعدّل المنشور السابق في مكانه. يعيد رسالة الحالة."""
        if not await cls.enabled():
            return "الميزة معطلة من مركز الإضافات."
        chat_id = await cls.channel_chat_id()
        if chat_id is None:
            return "لم تُضبط قناة التوفر بعد (من إدارة خدمات الأرقام ← 📡 قناة التوفر)."

        built = await cls.build_rows(bot=bot)
        if built is None:
            if cls._last_skip_reason == "username_unresolved":
                return "تعذّر تحديد يوزرنيم البوت — تأكد أن للبوت يوزرنيم عام."
            # لا توجد دول متاحة: نمسح المنشور حتى لا يبقى زر شراء ميت.
            await cls._delete_last(bot, chat_id)
            if cls._last_skip_reason == "service_unavailable":
                return "خدمة الأرقام المختارة غير مفعّلة."
            return "لا توجد دول متوفرة الآن — أُزيلت اللوحة القديمة إن وُجدت."

        text, rows = built
        service_code = await cls.service_code()
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        # نفس اليوزرنيم الحيّ المستخدم في أزرار الدول — لا قيمة بيئة قديمة.
        bot_username = await resolve_bot_username(bot)
        keyboard = [[InlineKeyboardButton(text=label, url=url)] for label, url in rows]
        if bot_username:
            keyboard.append(
                [InlineKeyboardButton(text="📱 دخول البوت", url=f"https://t.me/{bot_username}")]
            )
        markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

        # 1) القرار: تعديل في المكان أم «رسالة جديدة» تُصدر إشعاراً؟
        #    Telegram لا يُشعر المشتركين بتعديل رسالة، والرسالة المُعدَّلة
        #    تبقى مدفونة تحت أي منشور أحدث في القناة. لذلك تُعاد اللوحة
        #    كرسالة جديدة كل عدد محدد من الدورات (auto_repost)،
        #    أو فوراً عند رجوع دولة نادرة إن فعّل الأدمن ذلك.
        cycles = cls._cycles_since_post.get(service_code, 0)
        needs_new_post = cls._last_post is None or (
            await cls.auto_repost() and (cycles + 1) >= (await cls.repost_every_cycles())
        )
        if not needs_new_post and await cls.repost_on_restock() and cls._last_restock_count:
            needs_new_post = True

        if needs_new_post:
            await cls._delete_last(bot, chat_id)

        # 2) محاولة التعديل في المكان: يبقي التفاعلات ولا يزعج المشتركين.
        if cls._last_post and cls._last_post[0] == chat_id:
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=cls._last_post[1],
                    text=text,
                    reply_markup=markup,
                )
                cls._last_text = text
                cls._cycles_since_post[service_code] = cycles + 1
                return f"✅ حُدِّثت لوحة التوفر ({len(rows)} دولة)."
            except Exception as exc:  # noqa: BLE001 - نعيد النشر عند الفشل
                message = str(exc).lower()
                if "message is not modified" in message:
                    cls._cycles_since_post[service_code] = cycles + 1
                    return "لا تغيير في اللوحة (المحتوى مطابق)."
                logger.debug("تعذّر تعديل لوحة التوفر، سيُعاد نشرها: %s", exc)
                await cls._delete_last(bot, chat_id)

        message = await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        cls._last_post = (chat_id, message.message_id)
        cls._last_text = text
        cls._cycles_since_post[service_code] = 0
        return f"✅ نُشرت لوحة التوفر ({len(rows)} دولة)."

    @classmethod
    async def _delete_last(cls, bot, chat_id: int) -> None:
        if cls._last_post and cls._last_post[0] == chat_id:
            try:
                await bot.delete_message(chat_id=chat_id, message_id=cls._last_post[1])
            except Exception:  # noqa: BLE001
                logger.debug("تعذر حذف رسالة التوفر السابقة (ربما حُذفت يدوياً)")
            cls._last_post = None
            cls._last_text = ""

    @classmethod
    async def repost_now(cls, bot) -> str:
        """إجبار إعادة نشر جديدة (يستخدمها الأدمن لرفع اللوحة لأعلى القناة)."""
        chat_id = await cls.channel_chat_id()
        if chat_id is not None:
            await cls._delete_last(bot, chat_id)
        return await cls.post_board(bot)
