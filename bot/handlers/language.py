"""تبديل لغة واجهة المستخدم (العربية/الإنجليزية)."""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from database.models import User
from keyboards.language import language_kb
from services.i18n_service import I18nService

router = Router(name="language")


@router.callback_query(F.data == "menu:language")
async def language_menu(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🌐 <b>اختر اللغة</b>\n<b>Choose your language</b>",
        reply_markup=language_kb(),
    )


@router.callback_query(F.data.startswith("language:set:"))
async def language_set(callback: CallbackQuery, session, db_user: User):
    language = callback.data.split(":")[2]
    if language not in ("ar", "en"):
        await callback.answer("Invalid language", show_alert=True)
        return
    db_user.language_code = language
    await session.commit()
    await callback.answer(I18nService.t("language_updated", language), show_alert=True)
    default_name = "friend" if language == "en" else "عزيزي"
    name = (db_user.full_name or default_name) if db_user else default_name
    await callback.message.edit_text(
        I18nService.t("welcome", language, name=name),
        reply_markup=language_kb(),
    )
