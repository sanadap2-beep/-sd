"""
إذا كان المستخدم داخل تسلسل FSM (مثلاً بمنتصف شحن رصيد)
وضغط على زر من القائمة الرئيسية أو أرسل أمراً (/)،
هذا الميدلوير يصفّر حالته تلقائياً.

لا يُصفَّر معالج لوحة الأدمن عند أزرار نفس المعالج
(مثل admin:aprov_curr:USD) وإلا تضيع بيانات إضافة المزود.
"""

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

_USER_ESCAPE_CALLBACKS = frozenset({"check_subscription", "back_to_main"})


def _is_admin_fsm(state_name: str | None) -> bool:
    """aiogram stores states as ``AdminApiProviderStates:waiting_currency``."""
    if not state_name:
        return False
    return state_name.split(":", 1)[0].startswith("Admin")


def should_reset_fsm(
    current_state: str | None,
    *,
    is_slash_command: bool = False,
    callback_data: str | None = None,
) -> bool:
    """Whether the current FSM should be cleared before the handler runs."""
    if not current_state:
        return False
    if is_slash_command:
        return True
    if not callback_data:
        return False
    if callback_data.startswith("menu:") or callback_data in _USER_ESCAPE_CALLBACKS:
        return True
    if callback_data.startswith("admin:"):
        # User flows (deposit, orders, …) leave when opening the admin panel.
        if not _is_admin_fsm(current_state):
            return True
        # Admin wizards keep their data on step buttons such as
        # ``admin:aprov_curr:USD`` / ``admin:aprov_test``.
        # Returning to the admin home still cancels the wizard.
        return callback_data == "admin:main" or callback_data.startswith("admin:main:")
    return False


class StateResetMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        state = data.get("state")
        current = await state.get_state() if state else None

        is_slash_command = False
        callback_data = None
        if isinstance(event, Message):
            is_slash_command = bool(event.text and event.text.startswith("/"))
        elif isinstance(event, CallbackQuery):
            callback_data = event.data

        if state and should_reset_fsm(
            current,
            is_slash_command=is_slash_command,
            callback_data=callback_data,
        ):
            await state.clear()

        return await handler(event, data)
