"""Fallback handlers for unknown buttons."""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from handlers.start import _build_menu, _main_header
from services.notification_service import NotificationService

router = Router(name="fallback")


@router.callback_query(F.data == "menu:main")
async def menu_main_alias(callback: CallbackQuery, session, db_user):
    """Compatibility alias for old buttons that used menu:main."""
    await callback.answer()
    menu_kb = await _build_menu(session, db_user)
    header = await _main_header(session, db_user)
    try:
        await callback.message.edit_text(header, reply_markup=menu_kb)
    except Exception:
        await callback.message.answer(header, reply_markup=menu_kb)


@router.callback_query(F.data)
async def unknown_callback(callback: CallbackQuery, bot):
    data = callback.data or ""
    await callback.answer("⚠️ هذا الزر غير متاح حالياً أو انتهت صلاحيته.", show_alert=True)
    await NotificationService(bot).notify_admin(
        "⚠️ <b>زر غير معالج</b>\n\n"
        f"Callback: <code>{data}</code>\n"
        f"المستخدم: <code>{callback.from_user.id}</code> @{callback.from_user.username or '-'}",
        notification_type="system",
        priority="normal",
        dedupe_key=f"unknown_callback:{data}",
    )
