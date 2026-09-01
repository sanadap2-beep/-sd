"""
إعدادات الأسعار ونسبة الربح.
"""

from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from services.settings_service import SettingsService
from states.states import AdminPricingStates
from keyboards.admin import admin_pricing_kb, admin_back_kb
from filters.admin_filter import IsAdmin

router = Router(name="admin_pricing")
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


@router.callback_query(F.data == "admin:pricing")
async def pricing_menu(callback: CallbackQuery):
    margin = await SettingsService.get_decimal("default_profit_margin_percent")
    await callback.message.edit_text(
        "💵 <b>إدارة الأسعار</b>\n\n"
        f"📈 نسبة الربح العامة الحالية: <b>{margin}%</b>\n\n"
        "هذه النسبة تُطبَّق على كل خدمة/دولة لا تملك "
        "نسبة مخصصة.\n"
        "يمكنك تعيين نسب مخصصة من صفحة تفاصيل كل خدمة.",
        reply_markup=admin_pricing_kb(),
    )


@router.callback_query(F.data == "admin:set_margin")
async def set_margin_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📈 أرسل نسبة الربح العامة الجديدة (%):",
        reply_markup=admin_back_kb(),
    )
    await state.set_state(AdminPricingStates.waiting_margin_value)


@router.message(AdminPricingStates.waiting_margin_value)
async def set_margin_received(message: Message, state: FSMContext, session):
    try:
        margin = Decimal((message.text or "").strip())
        if not margin.is_finite() or margin < 0:
            raise InvalidOperation
    except InvalidOperation:
        await message.answer("⚠️ أرسل رقماً صحيحاً.")
        return

    await SettingsService.set(
        session,
        "default_profit_margin_percent",
        str(margin),
    )
    await message.answer(f"✅ تم تحديث نسبة الربح العامة إلى {margin}%")
    await state.clear()
