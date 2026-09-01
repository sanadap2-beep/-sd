"""
إذا كان المستخدم داخل تسلسل FSM (مثلاً بمنتصف شحن رصيد)
وضغط على زر من القائمة الرئيسية أو أرسل أمراً (/)،
هذا الميدلوير يصفّر حالته تلقائياً.

التحسين: بدل قائمة نصوص ثابتة، يتحقق من:
1) أي أمر يبدأ بـ /
2) أي callback يبدأ بـ menu: أو admin:
"""

from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery


class StateResetMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        should_reset = False

        if isinstance(event, Message):
            if event.text and event.text.startswith("/"):
                should_reset = True

        elif isinstance(event, CallbackQuery):
            if event.data and (
                event.data.startswith("menu:")
                or event.data.startswith("admin:")
                or event.data == "check_subscription"
                or event.data == "back_to_main"
            ):
                should_reset = True

        if should_reset:
            state = data.get("state")
            if state:
                current = await state.get_state()
                if current is not None:
                    await state.clear()

        return await handler(event, data)
