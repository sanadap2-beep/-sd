"""
لوحة تحكم الأدمن الرئيسية.
"""

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from keyboards.admin import admin_main_kb, admin_maintenance_kb
from services.settings_service import SettingsService
from states.states import AdminMaintenanceStates
from filters.admin_filter import IsAdmin

router = Router(name="admin_panel")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.message(Command("admin"))
async def admin_entry(message: Message):
    await message.answer(
        "🛠 <b>لوحة تحكم الأدمن</b>",
        reply_markup=admin_main_kb(),
    )


@router.callback_query(F.data == "admin:main")
async def admin_main_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "🛠 <b>لوحة تحكم الأدمن</b>",
        reply_markup=admin_main_kb(),
    )


# ══════════════ وضع الصيانة ══════════════


@router.callback_query(F.data == "admin:maintenance")
async def maintenance_menu(callback: CallbackQuery):
    is_active = await SettingsService.get_bool("maintenance_mode", False)
    current_msg = await SettingsService.get("maintenance_message", "⚙️ البوت تحت الصيانة حالياً...")
    status = "🔴 مفعّل" if is_active else "🟢 غير مفعّل"
    await callback.message.edit_text(
        f"🔧 <b>وضع الصيانة</b>\n\n"
        f"الحالة: {status}\n\n"
        f"📝 رسالة الصيانة الحالية:\n"
        f"<i>{current_msg}</i>",
        reply_markup=admin_maintenance_kb(is_active),
    )


@router.callback_query(F.data == "admin:maintenance_on")
async def maintenance_on(callback: CallbackQuery, session):
    await SettingsService.set(session, "maintenance_mode", "true")
    await callback.answer("✅ تم تفعيل وضع الصيانة.")
    await maintenance_menu(callback)


@router.callback_query(F.data == "admin:maintenance_off")
async def maintenance_off(callback: CallbackQuery, session):
    await SettingsService.set(session, "maintenance_mode", "false")
    await callback.answer("✅ تم إيقاف وضع الصيانة.")
    await maintenance_menu(callback)


@router.callback_query(F.data == "admin:maintenance_msg")
async def maintenance_msg_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📝 أرسل رسالة الصيانة الجديدة:")
    await state.set_state(AdminMaintenanceStates.waiting_message)


@router.message(AdminMaintenanceStates.waiting_message)
async def maintenance_msg_received(message: Message, state: FSMContext, session):
    await SettingsService.set(
        session,
        "maintenance_message",
        message.text.strip(),
    )
    await message.answer(
        "✅ تم تحديث رسالة الصيانة.",
        reply_markup=admin_main_kb(),
    )
    await state.clear()
