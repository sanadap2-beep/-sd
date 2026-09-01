"""
يتحقق من اشتراك المستخدم بكل القنوات الإجبارية قبل السماح باستخدام أي زر.
مستثنى منه: الأدمن، أمر /start، وزر "تحقق من الاشتراك" نفسه.
"""

from aiogram.types import Message, CallbackQuery

from services.subscription_service import SubscriptionService
from keyboards.common import check_subscription_kb


class SubscriptionMiddleware:
    def __init__(self, bot):
        self.bot = bot

    async def __call__(self, handler, event, data):
        db_user = data.get("db_user")
        session = data.get("session")

        if db_user is None or db_user.is_admin:
            return await handler(event, data)

        if isinstance(event, Message) and event.text and event.text.startswith("/start"):
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and event.data == "check_subscription":
            return await handler(event, data)

        is_ok, missing_channels = await SubscriptionService.is_user_subscribed_all(
            self.bot, session, db_user.telegram_id
        )

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
