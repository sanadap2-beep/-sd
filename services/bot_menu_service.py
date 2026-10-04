"""Bot menu button: /start for everyone (no /admin in the menu).

يُستدعى عند كل إقلاع: قائمة الأوامر الافتراضية = /start فقط
(عربي + إنجليزي)، وزر القائمة = MenuButtonCommands (يظهر «☰ القائمة»
بجانب حقل الإدخال مثل بوتات المتاجر). لا يُضاف /admin عمداً.
"""

from __future__ import annotations

import logging

from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands

logger = logging.getLogger(__name__)


async def sync_bot_menu(bot) -> bool:
    """يثبت زر القائمة والأوامر. يرجع True عند النجاح (غير قاتل عند الفشل)."""
    try:
        await bot.set_my_commands(
            [BotCommand(command="start", description="القائمة الرئيسية 🏠")],
            scope=BotCommandScopeDefault(),
        )
        try:
            await bot.set_my_commands(
                [BotCommand(command="start", description="Main menu 🏠")],
                scope=BotCommandScopeDefault(),
                language_code="en",
            )
        except Exception:
            pass
        try:
            await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        except Exception:
            pass
        return True
    except Exception:
        logger.warning("تعذر مزامنة زر قائمة البوت — سيُعاد عند الإقلاع التالي")
        return False
