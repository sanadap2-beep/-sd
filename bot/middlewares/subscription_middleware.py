"""
يتحقق من اشتراك المستخدم بكل القنوات الإجبارية قبل السماح باستخدام أي زر.
مستثنى منه: الأدمن، أمر /start، وزر "تحقق من الاشتراك" نفسه.

إصلاح الأداء:
كان الفحص يتصل بـ Telegram (getChatMember) عند كل رسالة أو ضغطة زر لكل
قناة إجبارية. مع 5 قنوات إجبارية و100 مستخدم نشط، يصل البوت بسرعة لحد
FloodControl (429) فيتجمد كلياً لعدة دقائق.
الآن تُخزَّن نتيجة الفحص في الذاكرة لمدة 60 ثانية لكل مستخدم:
- أول تفاعل بعد الاشتراك يفحص طازجاً ثم يُخزَّن.
- زر "تحقق من الاشتراك" يبقى فحصاً جديداً دائماً (لا يمر من الكاش).
- أي تغيير في القنوات الإجبارية من لوحة الأدمن ينعكس خلال 60 ثانية كحد أقصى.
"""

from __future__ import annotations

import time

from aiogram.types import Message, CallbackQuery

from services.subscription_service import SubscriptionService
from keyboards.common import check_subscription_kb

# مدة صلاحية نتيجة فحص الاشتراك في الذاكرة (بالثواني).
_CACHE_TTL_SECONDS = 60.0
# حد أقصى لحجم الكاش؛ فوقه ننظف النتائج القديمة.
_CACHE_PRUNE_THRESHOLD = 10_000


class SubscriptionMiddleware:
    _cache: dict[int, tuple[float, bool, list]] = {}

    def __init__(self, bot):
        self.bot = bot

    def _cached(self, user_id: int) -> tuple[bool, list] | None:
        entry = self._cache.get(user_id)
        if entry is None:
            return None
        checked_at, is_ok, missing = entry
        if time.monotonic() - checked_at >= _CACHE_TTL_SECONDS:
            self._cache.pop(user_id, None)
            return None
        return is_ok, missing

    def _store(self, user_id: int, is_ok: bool, missing: list) -> None:
        if len(self._cache) >= _CACHE_PRUNE_THRESHOLD:
            now = time.monotonic()
            for uid in [uid for uid, (ts, _, _) in self._cache.items() if now - ts >= _CACHE_TTL_SECONDS]:
                self._cache.pop(uid, None)
        self._cache[user_id] = (time.monotonic(), is_ok, missing)

    async def __call__(self, handler, event, data):
        db_user = data.get("db_user")
        session = data.get("session")

        if db_user is None or db_user.is_admin:
            return await handler(event, data)

        if isinstance(event, Message) and event.text and event.text.startswith("/start"):
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.data == "check_subscription":
            return await handler(event, data)

        cached = self._cached(db_user.telegram_id)
        if cached is not None:
            is_ok, missing_channels = cached
        else:
            is_ok, missing_channels = await SubscriptionService.is_user_subscribed_all(
                self.bot, session, db_user.telegram_id
            )
            self._store(db_user.telegram_id, is_ok, missing_channels)

        if not is_ok:
            text = "⚠️ يجب عليك الاشتراك بالقنوات التالية أولاً لاستخدام البوت:"
            kb = check_subscription_kb(missing_channels)
            if isinstance(event, Message):
                await event.answer(text, reply_markup=kb)
            else:
                await event.message.answer(text, reply_markup=kb)
                await event.answer()
            return

        return await handler(event, data)
